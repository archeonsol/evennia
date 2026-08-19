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

from django.apps import apps
from django.db import connection

from evennia.console import spec
from evennia.console.registry import Panel

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

        if not _is_postgres():
            return {
                "model": label,
                "key": key,
                "category": category,
                "exact": False,
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

        model = apps.get_model(label)
        if not _is_postgres():
            return {
                "model": label,
                "key": key,
                "rows": [],
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
            "note": "This query reads the whole table. Do not use it in a hot path.",
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
                {"key": key, "kind": _value_kind(value), "value": _preview(value, 400)}
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
