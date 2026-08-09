"""
JSONB attribute backend and public force-flush API.

Stores all of an object's attributes in a single ``db_attrs JSONField`` on
the object's own model row.  Reads serve an in-process L1 dict loaded once
per cache lifetime; writes update the L1 dict and register the backend in
``_DIRTY_BACKENDS`` so ``flush_all_dirty()`` (run by the flush-attributes
system on the system scheduler, plus once at shutdown) persists the document
in one ``UPDATE`` per dirty object.

Document layout
---------------
Top-level keys:

``"~"`` (``_NULL_CATEGORY``)
    Attributes with ``category=None``.

``"<category>"``
    Attributes in a named category.

Each category section is a dict with up to three sub-dicts:

``"_d"`` (*data*)
    ``{key: to_jsonb(value)}`` for every attribute.

``"_l"`` (*locks*, sparse)
    ``{key: lockstring}`` — only present when at least one attr has a lock.

``"_s"`` (*strvalues*, sparse)
    ``{key: strvalue}`` — only for nick attributes (strattr=True).

Force-flush API
---------------
Call ``force_flush(obj)`` to immediately write the object's document to DB
without waiting for the next maintenance tick.  Use for safety-critical
transitions (death state, combat end) where a crash must not lose the write.

Example::

    from evennia.typeclasses.jsonb_handler import force_flush

    character.db.death_state = INCAPACITATED
    force_flush(character)
"""

import json
import os
import tempfile
import time
import weakref
from collections.abc import Mapping
from copy import deepcopy

from django.conf import settings
from django.db import transaction

from evennia.typeclasses.attributes import _DIRTY_BACKENDS, IAttributeBackend, InMemoryAttribute
from evennia.typeclasses.jsonb_util import from_jsonb, to_jsonb

__all__ = (
    "JsonbAttributeBackend",
    "FlushResult",
    "force_flush",
    "has_spooled_write",
    "reclaim_spooled_writes",
    "spool_pending_count",
)

# Document sub-keys within each category section.
_NULL_CATEGORY = "~"
_DATA = "_d"
_LOCKS = "_l"
_STRV = "_s"


# ---------------------------------------------------------------------------
# Flush result and durable write spool
# ---------------------------------------------------------------------------


class FlushResult:
    """
    Typed outcome of a single attribute-document flush.

    Truthiness is *durability*: ``bool(result)`` is True when the write is
    safe somewhere — either it reached the database (:attr:`ok`) or it was
    diverted to the on-disk spool for later reclamation (:attr:`spooled`). A
    falsy result means the write is still only in the volatile L1 dict and
    would be lost on a crash; the backend stays dirty and will be retried.

    Safety-critical callers (death state, combat end) should check this and
    react to a non-durable result rather than assuming persistence.

    Attributes:
        ok (bool): The document reached the database this attempt.
        spooled (bool): The document was written to the durable spool instead
            (the DB write kept failing past the retry limit).
        error (Exception or None): The DB error, when the write did not land.
    """

    __slots__ = ("ok", "spooled", "error")

    def __init__(self, ok, spooled=False, error=None):
        self.ok = ok
        self.spooled = spooled
        self.error = error

    @property
    def durable(self):
        """True when the write is safe in the DB or the spool."""
        return bool(self.ok or self.spooled)

    def __bool__(self):
        return self.durable

    def __repr__(self):
        return f"<FlushResult ok={self.ok} spooled={self.spooled}>"


class JsonbWriteConflict(RuntimeError):
    """A stale write-behind edit conflicts with a newer database edit."""


_MISSING = object()


def _same(left, right):
    if left is _MISSING or right is _MISSING:
        return left is right
    return left == right


def _clone(value):
    return _MISSING if value is _MISSING else deepcopy(value)


def _three_way_merge(baseline, local, remote):
    """Merge non-overlapping document edits and reject exact-path conflicts."""
    if _same(local, baseline):
        return _clone(remote)
    if _same(remote, baseline) or _same(remote, local):
        return _clone(local)
    if baseline is _MISSING and isinstance(local, Mapping) and isinstance(remote, Mapping):
        baseline = {}
    if all(isinstance(value, Mapping) for value in (baseline, local, remote)):
        merged = {}
        for key in set(baseline) | set(local) | set(remote):
            value = _three_way_merge(
                baseline.get(key, _MISSING),
                local.get(key, _MISSING),
                remote.get(key, _MISSING),
            )
            if value is not _MISSING:
                merged[key] = value
        return merged
    if local is _MISSING and remote is _MISSING:
        return _MISSING
    raise JsonbWriteConflict("Attribute document changed concurrently at the same path.")


def _spool_dir():
    """
    Directory holding diverted (undurable) attribute documents.

    Configurable via ``settings.JSONB_WRITE_SPOOL_DIR``; defaults to
    ``<LOG_DIR>/jsonb_spool``.
    """
    configured = getattr(settings, "JSONB_WRITE_SPOOL_DIR", None)
    if configured:
        return configured
    log_dir = getattr(settings, "LOG_DIR", None) or tempfile.gettempdir()
    return os.path.join(log_dir, "jsonb_spool")


def _spool_write(obj, document, baseline=None):
    """
    Atomically persist ``document`` for ``obj`` to the on-disk spool.

    Last-resort durability when the database write keeps failing: the write is
    not lost, only deferred to :func:`reclaim_spooled_writes` on the next boot.

    Returns:
        bool: True if the document was durably written to disk.
    """
    pk = getattr(obj, "pk", None)
    if pk is None:
        return False
    try:
        model_label = obj._meta.label  # e.g. "objects.ObjectDB"
    except Exception:
        model_label = type(obj).__name__
    try:
        spool = _spool_dir()
        os.makedirs(spool, exist_ok=True)
        payload = {
            "model": model_label,
            "pk": pk,
            "db_attrs": document,
            "ts": time.time(),
        }
        if baseline is not None:
            payload["v"] = 2
            payload["baseline"] = baseline
        # write to a temp file in the same dir then atomically rename
        fd, tmp = tempfile.mkstemp(dir=spool, prefix=f"{model_label}.{pk}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            final = os.path.join(spool, f"{model_label}.{pk}.json")
            os.replace(tmp, final)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return True
    except Exception:
        from evennia.utils import logger

        logger.log_trace("jsonb spool: could not write spool file")
        return False


def has_spooled_write(obj):
    """Return whether a deferred whole-document write exists for one object."""
    pk = getattr(obj, "pk", None)
    if pk is None:
        return False
    try:
        model_label = obj._meta.label
    except Exception:
        model_label = type(obj).__name__
    return os.path.isfile(os.path.join(_spool_dir(), f"{model_label}.{pk}.json"))


def spool_remaining_dirty():
    """
    Last-resort: divert every still-dirty attribute document to the durable
    spool. Call at shutdown *after* a final :func:`flush_all_dirty` so a write
    the DB refused on the way out is captured on disk instead of lost (it is
    replayed by :func:`reclaim_spooled_writes` on the next boot).

    Returns:
        int: Number of documents spooled.
    """
    from evennia.utils import logger

    spooled = 0
    for backend in list(_DIRTY_BACKENDS):
        if not isinstance(backend, JsonbAttributeBackend) or not backend._dirty:
            continue
        if _spool_write(backend.obj, backend._l1, backend._baseline):
            backend._mark_clean()
            spooled += 1
        else:
            logger.log_err(
                "CRITICAL: could not spool dirty attribute doc for pk=%s at shutdown; "
                "write may be lost." % getattr(backend.obj, "pk", "?")
            )
    if spooled:
        logger.log_warn(
            "jsonb spool: diverted %d undurable attribute write(s) to disk at shutdown" % spooled
        )
    return spooled


def spool_pending_count():
    """Number of undurable documents currently waiting in the spool."""
    try:
        spool = _spool_dir()
        if not os.path.isdir(spool):
            return 0
        return sum(1 for name in os.listdir(spool) if name.endswith(".json"))
    except Exception:
        return 0


def reclaim_spooled_writes():
    """
    Replay any spooled attribute documents into the database.

    Call once at server start, before normal operation, so writes diverted to
    the spool during a past DB outage are not lost. Each successfully replayed
    document's spool file is removed; a file whose object no longer exists is
    dropped. Returns the number of documents reclaimed.
    """
    from django.apps import apps

    from evennia.utils import logger

    spool = _spool_dir()
    if not os.path.isdir(spool):
        return 0
    reclaimed = 0
    for name in sorted(os.listdir(spool)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(spool, name)
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            app_label, model_name = payload["model"].split(".")
            model = apps.get_model(app_label, model_name)
            if payload.get("v") == 2 and "baseline" in payload:
                with transaction.atomic():
                    row = (
                        model.objects.select_for_update()
                        .filter(pk=payload["pk"])
                        .values("db_attrs")
                        .first()
                    )
                    if row is None:
                        updated = 0
                    else:
                        merged = _three_way_merge(
                            payload["baseline"],
                            payload["db_attrs"],
                            row["db_attrs"] or {},
                        )
                        updated = model.objects.filter(pk=payload["pk"]).update(db_attrs=merged)
            else:
                logger.log_warn(
                    "jsonb spool: replaying legacy payload without conflict baseline: %s" % name
                )
                updated = model.objects.filter(pk=payload["pk"]).update(
                    db_attrs=payload["db_attrs"]
                )
            if updated == 0:
                logger.log_warn(
                    "jsonb spool: %s no longer exists; dropping spooled write" % payload["model"]
                )
            else:
                reclaimed += 1
            os.remove(path)
        except Exception:
            logger.log_trace(f"jsonb spool: failed to reclaim {name}; leaving it in place")
    if reclaimed:
        logger.log_info("jsonb spool: reclaimed %d deferred attribute write(s)" % reclaimed)
    return reclaimed


# ---------------------------------------------------------------------------
# Attribute object
# ---------------------------------------------------------------------------


class JsonbAttribute(InMemoryAttribute):
    """
    InMemoryAttribute extended with write-back to the JSONB L1 dict.

    When _Saver* proxies mutate the value in-place they call
    ``attr.value = self``.  This override intercepts that call and propagates
    the change back to the owning ``JsonbAttributeBackend``.
    """

    # Weak-ref back to the backend; set by _make_attr.
    _backend_ref = None

    # Override value property so mutations write back to L1.
    @property
    def value(self):
        return self.db_value

    @value.setter
    def value(self, new_value):
        self.db_value = new_value
        backend = self._backend_ref() if self._backend_ref is not None else None
        if backend is not None:
            backend._on_attr_value_changed(self.db_key, self.db_category, new_value)
        elif self._backend_ref is not None:
            from evennia.utils import logger

            logger.log_err(
                f"JsonbAttribute: backend evicted while attr {self.db_key!r} still "
                "referenced — in-place mutation lost. Do not hold strong refs to "
                "JsonbAttribute objects after the owning object leaves the idmapper."
            )

    @value.deleter
    def value(self):
        pass  # deletion goes through handler.delete_attribute, not this path

    @property
    def lock_storage(self):
        return self.db_lock_storage

    @lock_storage.setter
    def lock_storage(self, value):
        self.db_lock_storage = value
        backend = self._backend_ref() if self._backend_ref is not None else None
        if backend is not None:
            backend._on_lock_changed(self.db_key, self.db_category, value)
        elif self._backend_ref is not None:
            from evennia.utils import logger

            logger.log_err(
                f"JsonbAttribute: backend evicted while attr {self.db_key!r} still "
                "referenced — lock mutation lost."
            )

    @lock_storage.deleter
    def lock_storage(self):
        self.lock_storage = ""


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class JsonbAttributeBackend(IAttributeBackend):
    """
    Attribute backend that stores everything in ``obj.db_attrs`` (JSONField).

    Enabled by setting in ``settings.py``::

        ATTRIBUTE_BACKEND_CLASS = "evennia.typeclasses.jsonb_handler.JsonbAttributeBackend"

    The old ``AttributeDB`` M2M table is left intact and is used for backfill
    and parity checking (see the management commands).  It is not read or
    written once this backend is active.

    Each backend instance lives for the lifetime of the object's idmapper
    cache entry.  The L1 dict (``_l1``) is loaded once on construction from
    ``obj.db_attrs`` and stays coherent until the object is evicted from the
    idmapper.
    """

    _attrclass = JsonbAttribute

    _FLUSH_FAIL_LIMIT = 10

    def __init__(self, handler, attrtype):
        super().__init__(handler, attrtype)
        self._dirty = False
        self._flush_failures = 0
        self._dirty_since = None
        self._pk_counter = 0
        self._l1 = self._load_document()
        # Preserve the exact database document that this write-behind view was
        # loaded from. Transactional consumers can use it for a lock-first
        # three-way merge instead of blindly flushing a stale whole document.
        self._baseline = deepcopy(self._l1)

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    def _load_document(self):
        raw = getattr(self.obj, "db_attrs", None)
        if not isinstance(raw, dict):
            return {}
        return raw

    def _cat_key(self, category):
        cat = _NULL_CATEGORY if category is None else category.lower()
        if self._attrtype:
            return f"_t_{self._attrtype}__{cat}"
        return cat

    def _get_section(self, category, *, create=False):
        key = self._cat_key(category)
        if key not in self._l1:
            if not create:
                return None
            self._l1[key] = {}
        return self._l1[key]

    def _mark_dirty(self):
        if not self._dirty:
            self._dirty_since = time.monotonic()
        self._dirty = True
        _DIRTY_BACKENDS.add(self)

    # ------------------------------------------------------------------
    # Write-back callbacks (called by JsonbAttribute on mutation)
    # ------------------------------------------------------------------

    def _on_attr_value_changed(self, key, category, value):
        section = self._get_section(category, create=True)
        section.setdefault(_DATA, {})[key] = to_jsonb(value)
        self._mark_dirty()

    def _on_lock_changed(self, key, category, lockstring):
        section = self._get_section(category, create=True)
        if lockstring:
            section.setdefault(_LOCKS, {})[key] = lockstring
        else:
            section.get(_LOCKS, {}).pop(key, None)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Attribute construction
    # ------------------------------------------------------------------

    def _make_attr(self, key, category, encoded, lockstring, strvalue):
        self._pk_counter += 1
        attr = JsonbAttribute(
            pk=self._pk_counter,
            key=key,
            category=category,
            lock_storage=lockstring or "",
        )
        attr.db_strvalue = strvalue
        # Pass db_obj=attr so _Saver* write-backs call attr.value = self.
        attr.db_value = from_jsonb(encoded, db_obj=attr)
        attr._backend_ref = weakref.ref(self)
        return attr

    # ------------------------------------------------------------------
    # IAttributeBackend queries
    # ------------------------------------------------------------------

    def query_all(self):
        attrs = []
        attrtype_prefix = f"_t_{self._attrtype}__" if self._attrtype else None
        for cat_key, section in self._l1.items():
            if not isinstance(section, dict):
                continue
            if attrtype_prefix:
                # Attrtype backend: only yield sections for this attrtype.
                if not cat_key.startswith(attrtype_prefix):
                    continue
                cat_part = cat_key[len(attrtype_prefix) :]
                category = None if cat_part == _NULL_CATEGORY else cat_part
            else:
                # Regular backend: skip internal meta and attrtype sections.
                if cat_key.startswith("_"):
                    continue
                category = None if cat_key == _NULL_CATEGORY else cat_key
            data = section.get(_DATA, {})
            locks = section.get(_LOCKS, {})
            strvs = section.get(_STRV, {})
            for k, enc in data.items():
                attrs.append(self._make_attr(k, category, enc, locks.get(k, ""), strvs.get(k)))
        return attrs

    def query_key(self, key, category):
        section = self._get_section(category)
        if not section:
            return []
        data = section.get(_DATA, {})
        if key not in data:
            return []
        locks = section.get(_LOCKS, {})
        strvs = section.get(_STRV, {})
        return [self._make_attr(key, category, data[key], locks.get(key, ""), strvs.get(key))]

    def query_category(self, category):
        section = self._get_section(category)
        if not section:
            return []
        data = section.get(_DATA, {})
        locks = section.get(_LOCKS, {})
        strvs = section.get(_STRV, {})
        return [self._make_attr(k, category, data[k], locks.get(k, ""), strvs.get(k)) for k in data]

    # ------------------------------------------------------------------
    # Cache layer — override to skip the M2M connector indirection
    # ------------------------------------------------------------------

    def _get_cache_key(self, key, category):
        """
        Override base _get_cache_key to use JsonbAttribute objects directly.

        The base implementation does ``conn[0].attribute`` which assumes
        query_key returns M2M through-table objects.  We return the attribute
        itself from query_key, so skip that indirection.
        """
        cachekey = (key, category)
        cachefound = False
        try:
            attr = settings.TYPECLASS_AGGRESSIVE_CACHE and self._cache[cachekey]
            cachefound = True
        except KeyError:
            attr = None

        if attr and attr.pk is None:
            attr = None
            cachefound = False
            del self._cache[cachekey]
        if cachefound and settings.TYPECLASS_AGGRESSIVE_CACHE:
            return [attr] if attr else []

        attrs = self.query_key(key, category)
        if attrs:
            attr = attrs[0]
            if settings.TYPECLASS_AGGRESSIVE_CACHE:
                self._cache[cachekey] = attr
            return [attr] if attr.pk is not None else []
        else:
            if settings.TYPECLASS_AGGRESSIVE_CACHE:
                self._cache[cachekey] = None
            return []

    # ------------------------------------------------------------------
    # IAttributeBackend mutations
    # ------------------------------------------------------------------

    def do_create_attribute(self, key, category, lockstring, value, strvalue):
        section = self._get_section(category, create=True)
        if strvalue:
            section.setdefault(_DATA, {})[key] = to_jsonb(None)
            section.setdefault(_STRV, {})[key] = value
        else:
            section.setdefault(_DATA, {})[key] = to_jsonb(value)
        if lockstring:
            section.setdefault(_LOCKS, {})[key] = lockstring
        self._mark_dirty()
        # Return a live attribute object so the handler can cache it.
        locks = section.get(_LOCKS, {})
        strvs = section.get(_STRV, {})
        data = section.get(_DATA, {})
        return self._make_attr(key, category, data[key], locks.get(key, ""), strvs.get(key))

    def do_update_attribute(self, attr, value, strvalue):
        key = attr.db_key
        category = attr.db_category
        section = self._get_section(category, create=True)
        if strvalue:
            section.setdefault(_DATA, {})[key] = to_jsonb(None)
            section.setdefault(_STRV, {})[key] = value
            attr.db_strvalue = value
            attr.db_value = None
        else:
            section.setdefault(_DATA, {})[key] = to_jsonb(value)
            attr.db_value = from_jsonb(section[_DATA][key], db_obj=attr)
            attr.db_strvalue = None
        self._mark_dirty()

    def do_batch_update_attribute(self, attr_obj, category, lock_storage, new_value, strvalue):
        old_cat = attr_obj.db_category
        # Move to new category if changed.
        if old_cat != category:
            old_section = self._get_section(old_cat)
            if old_section:
                old_section.get(_DATA, {}).pop(attr_obj.db_key, None)
                old_section.get(_LOCKS, {}).pop(attr_obj.db_key, None)
                old_section.get(_STRV, {}).pop(attr_obj.db_key, None)
            attr_obj.db_category = category
        attr_obj.db_lock_storage = lock_storage or ""
        self.do_update_attribute(attr_obj, new_value, strvalue)
        if lock_storage:
            section = self._get_section(category, create=True)
            section.setdefault(_LOCKS, {})[attr_obj.db_key] = lock_storage

    def do_batch_finish(self, attr_objs):
        pass  # all writes already applied in do_create / do_batch_update

    def batch_add(self, *args, **kwargs):
        """
        Override to fix a cache-coherence issue in the base implementation.

        IAttributeBackend.batch_add caches "not found" misses per key before
        calling do_create_attribute, which leaves stale None entries in _cache
        that shadow the freshly written _l1 data.  Clear the lookup cache so
        the next get() re-reads from _l1.
        """
        super().batch_add(*args, **kwargs)
        self._cache = {}
        self._catcache = {}
        self._cache_complete = False

    def do_delete_attribute(self, attr):
        key = attr.db_key
        category = attr.db_category
        section = self._get_section(category)
        if not section:
            return
        section.get(_DATA, {}).pop(key, None)
        section.get(_LOCKS, {}).pop(key, None)
        section.get(_STRV, {}).pop(key, None)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Flush (participates in the existing flush_all_dirty() loop)
    # ------------------------------------------------------------------

    def pending_count(self) -> int:
        return 1 if self._dirty else 0

    def dirty_age(self):
        """Seconds this backend has been continuously dirty (0.0 if clean)."""
        if not self._dirty or self._dirty_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self._dirty_since)

    def _mark_clean(self):
        self._dirty = False
        self._dirty_since = None
        self._flush_failures = 0
        _DIRTY_BACKENDS.discard(self)

    def mark_database_document(self, document):
        """Adopt one document known to have committed to the database."""
        self._l1 = deepcopy(document)
        self._baseline = deepcopy(document)
        self.obj.db_attrs = deepcopy(document)
        self._mark_clean()
        super().reset_cache()

    def merge_to_database(self):
        """Lock, three-way merge, and commit this backend without spool fallback."""
        if not self._dirty:
            return deepcopy(self._l1)
        baseline = deepcopy(self._baseline)
        local = deepcopy(self._l1)
        model = type(self.obj)
        with transaction.atomic():
            remote = (
                model.objects.select_for_update()
                .filter(pk=self.obj.pk)
                .values_list("db_attrs", flat=True)
                .get()
                or {}
            )
            document = _three_way_merge(baseline, local, remote)
            self.obj.db_attrs = document
            self.obj.save(update_fields=["db_attrs"])
            self.mark_database_document(document)
        return document

    def flush_dirty(self):
        """Write the L1 dict to ``db_attrs`` and save.  Called by the tick.

        Returns:
            FlushResult or None: The flush outcome, or ``None`` when there was
            nothing dirty to flush.
        """
        if not self._dirty:
            return None
        return self._do_flush()

    def _do_flush(self):
        """
        Persist the L1 document to the DB.

        Never marks the backend clean without durability: on repeated DB
        failure the write is diverted to the on-disk spool (durable) before
        the dirty flag is cleared; if even the spool write fails, the backend
        stays dirty so the write is retried rather than lost.

        Returns:
            FlushResult: The outcome (see :class:`FlushResult`).
        """
        from evennia.utils import logger

        try:
            self.merge_to_database()
            return FlushResult(ok=True)
        except JsonbWriteConflict as err:
            _DIRTY_BACKENDS.add(self)
            logger.log_warn(
                "JsonbAttributeBackend: refusing stale conflicting write for pk=%s."
                % getattr(self.obj, "pk", "?")
            )
            return FlushResult(ok=False, error=err)
        except Exception as err:
            self._flush_failures += 1
            _DIRTY_BACKENDS.add(self)
            past_limit = self._flush_failures >= self._FLUSH_FAIL_LIMIT
            logger.log_trace(
                f"JsonbAttributeBackend._do_flush: flush attempt #{self._flush_failures} "
                f"failed for pk={getattr(self.obj, 'pk', '?')}; "
                f"{'diverting to durable spool' if past_limit else 'will retry next tick'}."
            )
            if past_limit:
                # Last resort: divert to the durable spool so the write is not
                # lost, and only then clear dirty. If spooling also fails, keep
                # the backend dirty — never mark clean without durability.
                if _spool_write(self.obj, self._l1, self._baseline):
                    logger.log_err(
                        "JsonbAttributeBackend: DB write for pk=%s failed %d times; "
                        "diverted to durable spool (will reclaim on next boot)."
                        % (getattr(self.obj, "pk", "?"), self._flush_failures)
                    )
                    self._mark_clean()
                    return FlushResult(ok=False, spooled=True, error=err)
                logger.log_err(
                    "CRITICAL: JsonbAttributeBackend could not persist NOR spool "
                    "pk=%s after %d attempts; keeping dirty for retry."
                    % (getattr(self.obj, "pk", "?"), self._flush_failures)
                )
            return FlushResult(ok=False, error=err)

    def reset_cache(self):
        """Reload L1 from DB and reset the upper-layer cache."""
        self._l1 = self._load_document()
        self._baseline = deepcopy(self._l1)
        super().reset_cache()


# ---------------------------------------------------------------------------
# Public force-flush API
# ---------------------------------------------------------------------------


def force_flush(obj):
    """
    Immediately persist *obj*'s attribute document to DB.

    Use for safety-critical writes (death state, combat end) that must not
    be lost if the server crashes before the next maintenance tick.

    No-op when the JSONB backend is not active or the object has no dirty
    attribute changes.

    Args:
        obj (TypedObject): The object to flush.

    Returns:
        FlushResult: The flush outcome. A falsy result means the write did
        NOT reach durable storage (neither DB nor spool) and safety-critical
        callers should react (retry, alert, refuse to proceed). Returns a
        durable ``FlushResult(ok=True)`` no-op when the backend is inactive or
        there is nothing dirty to flush.
    """
    try:
        backend = obj.attributes.backend
    except AttributeError:
        return FlushResult(ok=True)
    if isinstance(backend, JsonbAttributeBackend):
        if not backend._dirty:
            return FlushResult(ok=True)
        return backend._do_flush()
    return FlushResult(ok=True)
