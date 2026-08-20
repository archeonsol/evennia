"""The attribute lens.

Django admin lost this entirely at the JSONB migration: the ``Attribute`` model
was deleted, and its ModelAdmin is a nine-line tombstone. Nothing has been able
to inspect an object's attributes from the web since.

**Keys are the navigation problem, not rows.** Attributes are a document per
object, so this engine has genuinely unbounded fields: any object may carry any
key. No relational column can show them, and no table of objects helps you find
the one key you care about. So this panel inverts the usual lens -- you browse
*keys* first, then reach objects through them.

Three costs shape the implementation.

**The catalogue cannot be exact.** No index can be asked to enumerate itself, so
listing which keys exist means reading documents. That is bounded to a sample
and labelled as one, rather than run as an unbounded scan behind a page load.

**Per-key counts can be exact, and cheap.** The GIN index is built with the
default ``jsonb_ops`` operator class, which supports containment (``@>``) and
jsonpath existence (``@?``). So once you name a key, its exact object count and
its matching objects come from the index.

**Document size is a real operational number.** Every flush serializes the whole
document, so one fat attribute inflates every unrelated write on that object.
Nothing before this showed which objects were fat.

"""

from __future__ import annotations

import json

from django.apps import apps
from django.db import connection

from evennia.console import audit, spec
from evennia.console.registry import Panel, io_action

#: Documents read to build the catalogue. The catalogue is a navigation aid,
#: not an audit: a sample large enough to surface every key an operator is
#: likely to be looking for costs far less than a table scan per page view.
SAMPLE_SIZE = 2000

#: Hard ceiling on rows returned by any list this panel produces.
MAX_ROWS = 500

#: Document byte size past which an object is worth an operator's attention.
#: Every attribute write on the object re-serializes the whole document, so a
#: large rarely-read value taxes every small frequently-written one.
FAT_DOCUMENT_BYTES = 4096

#: The engine's document layout. Top level is category, ``"~"`` for the default
#: (uncategorised) one; ``_d`` holds values, ``_l`` locks, ``_s`` strvalues.
NULL_CATEGORY = "~"
DATA = "_d"

#: Prefix the JSONB codec puts on a value it had to pickle because JSON could
#: not carry it (``evennia.typeclasses.jsonb_util``). Rendering the base64 body
#: teaches an operator nothing, so the panel names the shape instead.
PACKED_PREFIX = "__P:"


def _is_postgres() -> bool:
    """Return whether the default connection can serve JSONB operators."""

    return connection.vendor == "postgresql"


def attribute_models() -> tuple[str, ...]:
    """Return the concrete models that carry an attribute document.

    Proxies are excluded. They share their concrete model's table, so offering
    every proxy would show one set of documents under a dozen names and make
    the picker harder to use rather than more complete.
    """

    return tuple(
        item.label
        for item in spec.model_specs()
        if not item.proxy and any(field.name == "db_attrs" for field in item.fields)
    )


def _is_packed(value) -> bool:
    """Return whether a stored value is a pickled Python object."""

    return isinstance(value, str) and value.startswith(PACKED_PREFIX)


def _value_kind(value) -> str:
    """Name one stored value's shape for the catalogue."""

    if _is_packed(value):
        return "packed"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    return type(value).__name__


def _preview(value, limit: int = 120) -> str:
    """Render one stored value as a short, safe preview string."""

    if _is_packed(value):
        return "(packed Python object)"
    text = str(value)
    return text[:limit] + ("..." if len(text) > limit else "")


#: How deep a value is walked before the panel stops descending. A stored
#: value can nest arbitrarily; a viewer cannot, and an operator who needs to
#: read past this depth needs the REPL, not a wider tree.
MAX_TREE_DEPTH = 6

#: Child nodes rendered per container before the rest are counted instead.
MAX_TREE_CHILDREN = 200


def _tree(value, depth: int = 0):
    """Render one stored value as a navigable node.

    ``_preview`` answers "what is roughly in here" in one line, which is right
    for a catalogue row and wrong for the thing P3 called a document view: a
    dict of dicts flattened to ``"{'a': {'b': ...}}"`` truncated at 400
    characters is not readable and not navigable. This walks the value instead,
    so a nested structure can be opened rather than squinted at.

    Recursion is bounded twice, by depth and by breadth, because the value is
    operator data rather than engine data and nothing constrains its shape.

    Args:
        value: The decoded stored value.
        depth: Current recursion depth.

    Returns:
        dict: A node carrying its kind, a one-line summary, and its children.
    """

    kind = _value_kind(value)
    node = {"kind": kind, "summary": _preview(value, 160), "children": [], "truncated": 0}

    if kind == "packed":
        # The base64 body of a pickle teaches an operator nothing. Naming the
        # shape is the honest rendering.
        node["summary"] = "(packed Python object)"
        return node

    if depth >= MAX_TREE_DEPTH and kind in ("dict", "list"):
        node["summary"] = f"({kind}, not walked past depth {MAX_TREE_DEPTH})"
        return node

    if kind == "dict":
        items = list(value.items())
        node["summary"] = f"{{{len(items)} keys}}"
        for key, child in items[:MAX_TREE_CHILDREN]:
            entry = _tree(child, depth + 1)
            entry["name"] = str(key)[:120]
            node["children"].append(entry)
        node["truncated"] = max(0, len(items) - MAX_TREE_CHILDREN)
    elif kind == "list":
        node["summary"] = f"[{len(value)} items]"
        for index, child in enumerate(value[:MAX_TREE_CHILDREN]):
            entry = _tree(child, depth + 1)
            entry["name"] = str(index)
            node["children"].append(entry)
        node["truncated"] = max(0, len(value) - MAX_TREE_CHILDREN)

    return node


def _document_entries(document):
    """Yield ``(category, key, value)`` for one decoded attribute document.

    Args:
        document: The raw ``db_attrs`` value.

    Yields:
        tuple: Category name (``None`` for the default one), key, and value.
    """

    if not isinstance(document, dict):
        return
    for category, section in document.items():
        if not isinstance(section, dict):
            continue
        data = section.get(DATA)
        if not isinstance(data, dict):
            continue
        name = None if category == NULL_CATEGORY else category
        for key, value in data.items():
            yield name, key, value


class AttributesPanel(Panel):
    """Browse attribute keys, then reach the objects that carry them."""

    key = "attributes"
    label = "Attributes"
    description = "Which attribute keys exist, on what, and how large they are."
    columns = ("key", "category", "objects", "kinds")
    needs_io = False

    # -- catalogue -----------------------------------------------------

    def rows(self, ctx):
        """Return the sampled key catalogue for one model.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``model``,
                ``category``, and ``search``.

        Returns:
            dict: Key rows, the sample the counts came from, and the models
            that carry attributes at all.
        """

        params = ctx.params
        label = self._model_label(params.get("model"))
        model = apps.get_model(label)
        wanted_category = (params.get("category") or "").strip().lower()
        search = (params.get("search") or "").strip().lower()

        documents = list(
            model._base_manager.order_by("-id").values_list("db_attrs", flat=True)[:SAMPLE_SIZE]
        )

        catalogue = {}
        for document in documents:
            seen = set()
            for category, key, value in _document_entries(document):
                slot = (category or "", key)
                entry = catalogue.setdefault(
                    slot,
                    {
                        "key": key,
                        "category": category or "",
                        "objects": 0,
                        "kinds": set(),
                        "example": "",
                    },
                )
                if slot not in seen:
                    entry["objects"] += 1
                    seen.add(slot)
                entry["kinds"].add(_value_kind(value))
                if not entry["example"]:
                    entry["example"] = _preview(value)

        rows = []
        for entry in catalogue.values():
            if wanted_category and entry["category"].lower() != wanted_category:
                continue
            if search and search not in entry["key"].lower():
                continue
            rows.append(
                {
                    "key": entry["key"],
                    "category": entry["category"],
                    "objects": entry["objects"],
                    "kinds": sorted(entry["kinds"]),
                    "example": entry["example"],
                }
            )
        rows.sort(key=lambda row: (-row["objects"], row["key"]))

        return {
            "model": label,
            "models": list(attribute_models()),
            "categories": sorted({row["category"] for row in rows}),
            "rows": rows[:MAX_ROWS],
            "key_count": len(rows),
            "sample": {
                "documents_read": len(documents),
                "limit": SAMPLE_SIZE,
                "complete": len(documents) < SAMPLE_SIZE,
                "barrier": {
                    "flushed": False,
                    "reason": (
                        "The catalogue does not force a write-behind flush, so an "
                        "attribute set in the last few seconds may not appear yet. "
                        "Open the key to run a query that does."
                    ),
                },
                "note": (
                    "Counts come from the whole table."
                    if len(documents) < SAMPLE_SIZE
                    else f"Counts come from the {len(documents)} most recent rows. "
                    "Ask for one key to get its exact count."
                ),
            },
        }

    # -- one key -------------------------------------------------------

    def key_stats(self, ctx, model=None, key=None, category=None):
        """Return the exact object count for one attribute key.

        Uses jsonpath existence, which the default ``jsonb_ops`` GIN index
        serves, so this stays cheap on a large table even though the catalogue
        that led here was a sample.

        Args:
            ctx: Worker context carrying ``model``, ``key``, and optional
                ``category``.

        Returns:
            dict: The exact count, or an explanation of why it is unavailable.
        """

        label = self._model_label(model)
        key = str(key or "").strip()
        if not key:
            raise LookupError("an attribute key is required")
        category = str(category or "").strip().lower() or NULL_CATEGORY
        barrier = self._barrier()

        if not _is_postgres():
            return {
                "model": label,
                "key": key,
                "category": category,
                "exact": False,
                "barrier": barrier,
                "reason": (
                    "Exact counts need PostgreSQL. This deployment uses "
                    f"{connection.vendor}, which has no JSONB index to ask."
                ),
            }

        model = apps.get_model(label)
        table = model._meta.db_table
        # Parameterized jsonpath. The key is bound, never interpolated.
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT COUNT(*) FROM "{table}" '  # noqa: S608 - table from the app registry
                "WHERE db_attrs @? CAST(FORMAT('$.%I.%I.%I', %s, %s, %s) AS jsonpath)",
                [category, DATA, key],
            )
            (count,) = cursor.fetchone()
        return {
            "model": label,
            "key": key,
            "category": category,
            "objects": int(count),
            "exact": True,
            "barrier": barrier,
        }

    def find(self, ctx, model=None, key=None, value=None, category=None):
        """Return objects whose attribute key holds one exact value.

        A containment query against the GIN index, which is the shape
        ``performance.md`` blesses for admin and analytics work. It is a full
        scan for selectivity the index cannot help with, so it is capped and
        labelled rather than offered as a hot path.

        Args:
            ctx: Worker context carrying ``model``, ``key``, ``value``, and
                optional ``category``.

        Returns:
            dict: Matching object identifiers and keys.
        """

        label = self._model_label(model)
        key = str(key or "").strip()
        if not key:
            raise LookupError("an attribute key is required")
        category = str(category or "").strip().lower() or NULL_CATEGORY
        barrier = self._barrier()

        model = apps.get_model(label)
        if not _is_postgres():
            return {
                "model": label,
                "key": key,
                "rows": [],
                "barrier": barrier,
                "supported": False,
                "reason": (
                    "Containment queries need PostgreSQL. This deployment uses "
                    f"{connection.vendor}."
                ),
            }

        queryset = model._base_manager.filter(db_attrs__contains={category: {DATA: {key: value}}})
        fields = ["id"] + (["db_key"] if self._has_field(model, "db_key") else [])
        rows = list(queryset.order_by("id").values(*fields)[:MAX_ROWS])
        return {
            "model": label,
            "key": key,
            "category": category,
            "value": value,
            "rows": rows,
            "supported": True,
            "capped": len(rows) >= MAX_ROWS,
            "barrier": barrier,
            "note": "This query reads the whole table. Do not use it in a hot path.",
        }

    # -- the write-behind barrier --------------------------------------

    def _barrier(self):
        """Expose pending attribute writes, and report what that cost.

        Attribute writes are write-behind: ``attributes.add`` updates a row
        state and the ``flush-attributes`` system drains it on a cadence. Until
        it does, ``db_attrs`` in the database does not hold the value, so any
        query written against that column answers from before the write. An
        operator who just set a key and cannot find it would reasonably
        conclude the write failed.

        ``performance.md`` and ``objects/manager.py`` both treat an attribute
        search as hostile to game logic for exactly this reason: it forces a
        process-wide flush. The console is the legitimate caller -- staff
        tooling, not game logic -- but it still pays the cost, so the barrier
        is placed deliberately:

        **Point queries run it.** :meth:`carriers`, :meth:`find`, and
        :meth:`key_stats` each answer one question an operator asked, and a
        stale answer to those is a wrong answer.

        **The catalogue does not.** :meth:`rows` is already a bounded sample
        and says so; flushing the whole process on every render of a
        navigation aid would put a process-wide write on a page an operator
        leaves open.

        Returns:
            dict: What the flush drained, for the panel to report.
        """

        from evennia.typeclasses.attributes import flush_all_dirty

        try:
            stats = flush_all_dirty() or {}
        except Exception as err:  # noqa: BLE001 - a barrier failure is reportable
            return {"flushed": False, "reason": str(err)[:200]}
        return {
            "flushed": True,
            "pending": int(stats.get("pending", 0) or 0),
            "written": int(stats.get("total", 0) or 0),
        }

    def carriers(self, ctx, model=None, key=None, category=None):
        """Return the objects that carry one attribute key, with their values.

        The step between "which keys exist" and "what does this object hold".
        Without it the catalogue is a list of key names with no way to reach a
        single document from any of them.

        Distinct from :meth:`find`, which answers "which objects hold this key
        *set to this value*" and needs the value. An operator browsing the
        catalogue does not have a value yet; that is what they came to see.

        On PostgreSQL this is a jsonpath existence test, which the default
        ``jsonb_ops`` GIN index serves. Elsewhere there is no index to ask, so
        the same bounded sample the catalogue uses is scanned in Python and
        the result says so rather than implying it is complete.

        Args:
            ctx: Worker context carrying ``model``, ``key``, and optional
                ``category``.

        Returns:
            dict: Rows of ``id``, ``name``, and the value each one holds.

        Raises:
            LookupError: No key was given.
        """

        label = self._model_label(model)
        name = str(key or "").strip()
        if not name:
            raise LookupError("an attribute key is required")
        stored_category = str(category or "").strip().lower() or NULL_CATEGORY
        model = apps.get_model(label)
        has_key = self._has_field(model, "db_key")
        barrier = self._barrier()

        if _is_postgres():
            table = model._meta.db_table
            with connection.cursor() as cursor:
                # Parameterized jsonpath. The key is bound, never interpolated.
                cursor.execute(
                    f'SELECT id FROM "{table}" '  # noqa: S608 - table from the app registry
                    "WHERE db_attrs @? CAST(FORMAT('$.%I.%I.%I', %s, %s, %s) AS jsonpath) "
                    "ORDER BY id LIMIT %s",
                    [stored_category, DATA, name, MAX_ROWS],
                )
                ids = [row[0] for row in cursor.fetchall()]
            fields = ["id", "db_attrs"] + (["db_key"] if has_key else [])
            found = list(model._base_manager.filter(pk__in=ids).values(*fields))
            complete = len(ids) < MAX_ROWS
            note = "Matched through the JSONB index."
        else:
            fields = ["id", "db_attrs"] + (["db_key"] if has_key else [])
            documents = list(model._base_manager.order_by("-id").values(*fields)[:SAMPLE_SIZE])
            found = []
            for row in documents:
                for entry_category, entry_key, _value in _document_entries(row.get("db_attrs")):
                    stored = entry_category or NULL_CATEGORY
                    if entry_key == name and stored == stored_category:
                        found.append(row)
                        break
                if len(found) >= MAX_ROWS:
                    break
            complete = len(documents) < SAMPLE_SIZE
            note = (
                f"{connection.vendor} has no JSONB index to ask, so the "
                f"{len(documents)} most recent rows were read instead."
            )

        rows = []
        for row in found:
            value = None
            for entry_category, entry_key, entry_value in _document_entries(row.get("db_attrs")):
                if entry_key == name and (entry_category or NULL_CATEGORY) == stored_category:
                    value = entry_value
                    break
            rows.append(
                {
                    "id": row["id"],
                    "name": row.get("db_key", ""),
                    "value": _preview(value, 200),
                    "kind": _value_kind(value),
                }
            )

        return {
            "model": label,
            "key": name,
            "category": "" if stored_category == NULL_CATEGORY else stored_category,
            "rows": rows,
            "capped": len(rows) >= MAX_ROWS,
            "complete": complete,
            "barrier": barrier,
            "note": note,
        }

    # -- one object ----------------------------------------------------

    def detail(self, ctx, pk):
        """Return one object's decoded attribute document.

        Args:
            ctx: Worker context carrying ``model``.
            pk: Primary key of the object.

        Returns:
            dict: Entries grouped by category, plus the document's size.

        Raises:
            LookupError: No such object.
        """

        label = self._model_label(ctx.params.get("model"))
        model = apps.get_model(label)
        fields = ["id", "db_attrs"] + (["db_key"] if self._has_field(model, "db_key") else [])
        try:
            row = model._base_manager.filter(pk=pk).values(*fields).first()
        except (ValueError, TypeError) as err:
            raise LookupError(f"{pk!r} is not a valid identifier for {label}") from err
        if row is None:
            raise LookupError(f"no {label} with primary key {pk!r}")

        document = row.get("db_attrs") or {}
        groups = {}
        for category, key, value in _document_entries(document):
            groups.setdefault(category or "", []).append(
                {
                    "key": key,
                    "kind": _value_kind(value),
                    "value": _preview(value, 400),
                    "tree": _tree(value),
                    "editable": not _is_packed(value),
                }
            )
        for entries in groups.values():
            entries.sort(key=lambda entry: entry["key"])

        size = self._size(document)
        return {
            "model": label,
            "id": row["id"],
            "name": row.get("db_key", ""),
            "categories": [
                {"category": name, "entries": entries} for name, entries in sorted(groups.items())
            ],
            "entry_count": sum(len(entries) for entries in groups.values()),
            "size_bytes": size,
            "fat": size > FAT_DOCUMENT_BYTES,
            "size_note": (
                "Every attribute write on this object re-serializes the whole "
                "document. Move large rarely-read values to their own model field."
            ),
        }

    # -- document sizes ------------------------------------------------

    def sizes(self, ctx, model=None):
        """Return the largest attribute documents in the sample.

        ``performance.md`` states that a large static value inflates every
        unrelated write on the same object. Nothing before this let an operator
        check that rule against their own data.

        Args:
            ctx: Worker context carrying ``model``.

        Returns:
            dict: The largest documents seen, and how many exceed the
            attention threshold.
        """

        label = self._model_label(model)
        model = apps.get_model(label)
        fields = ["id", "db_attrs"] + (["db_key"] if self._has_field(model, "db_key") else [])
        sample = list(model._base_manager.order_by("-id").values(*fields)[:SAMPLE_SIZE])

        rows = []
        fat = 0
        for row in sample:
            size = self._size(row.get("db_attrs") or {})
            if size > FAT_DOCUMENT_BYTES:
                fat += 1
            rows.append(
                {
                    "id": row["id"],
                    "name": row.get("db_key", ""),
                    "size_bytes": size,
                    "fat": size > FAT_DOCUMENT_BYTES,
                }
            )
        rows.sort(key=lambda row: -row["size_bytes"])
        return {
            "model": label,
            "rows": rows[:100],
            "fat_count": fat,
            "threshold_bytes": FAT_DOCUMENT_BYTES,
            "sample": {"documents_read": len(sample), "limit": SAMPLE_SIZE},
        }

    # -- helpers -------------------------------------------------------

    # -- writes --------------------------------------------------------

    def _handled(self, label, pk):
        """Return one live object that carries an attribute handler.

        Raises:
            LookupError: No such row, or the model carries no handler.
        """

        model = apps.get_model(label)
        try:
            obj = model._base_manager.filter(pk=int(pk)).first()
        except (TypeError, ValueError) as err:
            raise LookupError(f"{pk!r} is not a valid identifier for {label}") from err
        if obj is None:
            raise LookupError(f"no {label} with primary key {pk!r}")
        if not hasattr(obj, "attributes"):
            raise LookupError(f"{label} rows carry no attribute handler")
        return obj

    def _decode(self, raw):
        """Parse one submitted value from JSON text.

        JSON rather than a guess. An operator who types ``123`` means the
        number and an operator who types ``"123"`` means the string, and a
        panel that decides for them will silently store the wrong type in a
        document nothing else validates.

        Args:
            raw: The submitted text.

        Returns:
            The decoded value.

        Raises:
            ValueError: The text is not valid JSON.
        """

        text = "" if raw is None else str(raw)
        if not text.strip():
            raise ValueError("a value is required; use null for an empty one")
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise ValueError(
                f"that is not valid JSON ({err.msg} at position {err.pos}). "
                'Quote a string as "text"; write a number bare.'
            ) from err

    def _current(self, obj, key, category):
        """Return the stored value and whether the key exists at all.

        Existence is asked of ``has()`` rather than inferred from ``get()``.
        Passing a sentinel as ``default`` does not work here: the handler
        returns a default wrapped in a one-item list, so the sentinel never
        compares equal to itself and every key looks like it already existed.
        """

        exists = bool(obj.attributes.has(key, category=category))
        if not exists:
            return None, False
        return obj.attributes.get(key, category=category), True

    @io_action
    def set(self, ctx, model=None, pk=None, key=None, category=None, value=None, reason=""):
        """Write one attribute through the object's own handler.

        Runs on the IO owner. This is not a preference: reading or writing
        ``db_attrs`` off the IO thread crashes on PostgreSQL while appearing to
        work on a SQLite development install, so the boundary is the difference
        between a panel that works and a panel that works until it is deployed.

        The handler is used rather than the column. A direct JSONB write would
        skip the codec that decides what can be stored as JSON and what has to
        be packed, and skip the cache invalidation that keeps other readers
        from serving the old value.

        Args:
            ctx: IO context.
            model: Model label.
            pk: Row identifier.
            key: Attribute key.
            category: Attribute category, or ``None`` for the default one.
            value: The new value, as JSON text.
            reason: Why, recorded in the audit row.

        Returns:
            dict: What was stored, and what was there before.

        Raises:
            LookupError: No such row, or no attribute handler.
            ValueError: No key was given, or the value is not valid JSON.
        """

        name = str(key or "").strip()
        if not name:
            raise ValueError("an attribute key is required")
        label = self._model_label(model)
        category = str(category).strip() or None if category else None
        decoded = self._decode(value)

        obj = self._handled(label, pk)
        previous, existed = self._current(obj, name, category)
        obj.attributes.add(name, decoded, category=category)

        stored, _ = self._current(obj, name, category)
        payload = {
            "model": label,
            "pk": obj.pk,
            "key": name,
            "category": category or "",
            "created": not existed,
            "before": _preview(previous, 400) if existed else "",
            "after": _preview(stored, 400),
            "kind": _value_kind(stored),
            "size_bytes": self._size(getattr(obj, "db_attrs", None) or {}),
        }
        payload["fat"] = payload["size_bytes"] > FAT_DOCUMENT_BYTES
        audit.record(
            panel=self.key,
            operation="add" if not existed else "change",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{label}#{obj.pk}:{name}"[:160],
            before={"value": payload["before"], "existed": existed},
            after={"value": payload["after"], "kind": payload["kind"]},
            message=str(reason or "")[:500],
        )
        return payload

    @io_action
    def unset(self, ctx, model=None, pk=None, key=None, category=None, reason=""):
        """Remove one attribute through the object's own handler.

        Args:
            ctx: IO context.
            model: Model label.
            pk: Row identifier.
            key: Attribute key.
            category: Attribute category, or ``None`` for the default one.
            reason: Why, recorded in the audit row.

        Returns:
            dict: What was removed.

        Raises:
            LookupError: No such row, no handler, or no such attribute.
            ValueError: No key was given.
        """

        name = str(key or "").strip()
        if not name:
            raise ValueError("an attribute key is required")
        label = self._model_label(model)
        category = str(category).strip() or None if category else None

        obj = self._handled(label, pk)
        previous, existed = self._current(obj, name, category)
        if not existed:
            # Reported rather than passed through. The handler removes quietly
            # when nothing matches, and an operator who mistyped a key would
            # otherwise be told the removal succeeded.
            raise LookupError(
                f"{label}#{obj.pk} carries no attribute {name!r}"
                + (f" in category {category!r}" if category else "")
            )
        obj.attributes.remove(name, category=category)

        audit.record(
            panel=self.key,
            operation="delete",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{label}#{obj.pk}:{name}"[:160],
            before={"value": _preview(previous, 400), "kind": _value_kind(previous)},
            after={},
            message=str(reason or "")[:500],
        )
        return {
            "model": label,
            "pk": obj.pk,
            "key": name,
            "category": category or "",
            "removed": _preview(previous, 400),
            "size_bytes": self._size(getattr(obj, "db_attrs", None) or {}),
        }

    def _model_label(self, label):
        """Return a validated attribute-carrying model label.

        Raises:
            LookupError: The model is unknown or carries no attributes.
        """

        labels = attribute_models()
        if not labels:
            raise LookupError("no installed model carries an attribute document")
        wanted = str(label or "").strip().lower() or labels[0]
        if wanted not in labels:
            raise LookupError(f"{wanted!r} does not carry an attribute document")
        return wanted

    def _has_field(self, model, name) -> bool:
        """Return whether one model declares a concrete field by name."""

        return any(field.name == name for field in model._meta.concrete_fields)

    def _size(self, document) -> int:
        """Return one document's serialized size in bytes."""

        import json

        try:
            return len(json.dumps(document, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return 0
