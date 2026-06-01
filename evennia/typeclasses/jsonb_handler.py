"""
JSONB attribute backend and public force-flush API.

Stores all of an object's attributes in a single ``db_attrs JSONField`` on
the object's own model row.  Reads serve an in-process L1 dict loaded once
per cache lifetime; writes update the L1 dict and register the backend in
``_DIRTY_BACKENDS`` so ``flush_all_dirty()`` (called each maintenance tick)
persists the document in one ``UPDATE`` per dirty object.

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

import weakref

from django.conf import settings

from evennia.typeclasses.attributes import (
    IAttributeBackend,
    InMemoryAttribute,
    _DIRTY_BACKENDS,
)
from evennia.typeclasses.jsonb_util import from_jsonb, to_jsonb

__all__ = ("JsonbAttributeBackend", "force_flush")

# Document sub-keys within each category section.
_NULL_CATEGORY = "~"
_DATA = "_d"
_LOCKS = "_l"
_STRV = "_s"


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
        self._pk_counter = 0
        self._l1 = self._load_document()

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
                cat_part = cat_key[len(attrtype_prefix):]
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
        return [
            self._make_attr(k, category, data[k], locks.get(k, ""), strvs.get(k))
            for k in data
        ]

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

    def flush_dirty(self):
        """Write the L1 dict to ``db_attrs`` and save.  Called by the tick."""
        if not self._dirty:
            return
        self._do_flush()

    def _do_flush(self):
        try:
            self.obj.db_attrs = self._l1
            self.obj.save(update_fields=["db_attrs"])
            self._dirty = False
            self._flush_failures = 0
            _DIRTY_BACKENDS.discard(self)
        except Exception:
            from evennia.utils import logger
            self._flush_failures += 1
            _DIRTY_BACKENDS.add(self)
            logger.log_trace(
                f"JsonbAttributeBackend._do_flush: flush attempt #{self._flush_failures} "
                f"failed for pk={getattr(self.obj, 'pk', '?')}; "
                f"{'giving up — write lost' if self._flush_failures >= self._FLUSH_FAIL_LIMIT else 'will retry next tick'}."
            )
            if self._flush_failures >= self._FLUSH_FAIL_LIMIT:
                self._dirty = False
                _DIRTY_BACKENDS.discard(self)

    def reset_cache(self):
        """Reload L1 from DB and reset the upper-layer cache."""
        self._l1 = self._load_document()
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
    """
    try:
        backend = obj.attributes.backend
    except AttributeError:
        return
    if isinstance(backend, JsonbAttributeBackend):
        backend._do_flush()
