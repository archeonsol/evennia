"""
Redis L2 cache for ModelAttributeBackend (Phase 2).

PostgreSQL remains source of truth. On Redis failure, all operations fall back to PG.
Cache hits hydrate Attribute instances from JSON without a database round-trip.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from django.conf import settings

from evennia.typeclasses.attributes import ModelAttributeBackend
from evennia.utils import logger
from evennia.utils.picklefield import dbsafe_decode, dbsafe_encode
from evennia.utils.utils import to_str

_CACHE_VERSION = "v2"
_LOG_INTERVAL = 60.0
_last_redis_warn = 0.0
_MISSING_MARKER = "__missing__"


def _enabled():
    return getattr(settings, "ATTRIBUTE_REDIS_CACHE_ENABLED", False)


def _ttl():
    return int(getattr(settings, "ATTRIBUTE_REDIS_CACHE_TTL", 3600))


def _redis_alias():
    return getattr(settings, "ATTRIBUTE_REDIS_CACHE_ALIAS", "attributes")


def _redis_conn():
    global _last_redis_warn
    try:
        from django_redis import get_redis_connection

        return get_redis_connection(_redis_alias())
    except Exception as exc:
        now = time.time()
        if now - _last_redis_warn >= _LOG_INTERVAL:
            _last_redis_warn = now
            logger.log_warn(f"Attribute Redis L2 unavailable, using PG only: {exc}")
        return None


def _redis_key(model, obj_id, key, category):
    cat = category if category is not None else ""
    return f"attr:{_CACHE_VERSION}:{model}:{obj_id}:{to_str(key).lower()}:{cat}"


def _obj_index_key(model, obj_id):
    return f"attr:{_CACHE_VERSION}:{model}:{obj_id}:__index__"


def _category_index_key(model, obj_id, category):
    cat = category if category is not None else ""
    return f"attr:{_CACHE_VERSION}:{model}:{obj_id}:__cat__:{cat}"


def _encode_attr(attr):
    payload = {
        "pk": attr.pk,
        "db_key": attr.db_key,
        "db_category": attr.db_category,
        "db_model": attr.db_model,
        "db_attrtype": attr.db_attrtype,
        "db_lock_storage": attr.db_lock_storage or "",
        "db_val_type": attr.db_val_type or "",
        "db_int_val": attr.db_int_val,
        "db_float_val": attr.db_float_val,
        "db_str_val": attr.db_str_val,
        "db_strvalue": attr.db_strvalue,
    }
    if attr.db_value is None:
        payload["db_value"] = None
    else:
        # db_value holds a Python object in packed-tuple form: _classify_value runs
        # to_pickle() on every write path, and PickledObjectField.from_db_value calls
        # dbsafe_decode (preserving packed tuples) on every PG load.  We encode it
        # back to a portable string using the same codec so dbsafe_decode on hydration
        # produces the identical in-memory state.  PickledObject is a str subclass so
        # json.dumps serialises it correctly without an explicit str() cast.
        payload["db_value"] = dbsafe_encode(attr.db_value)
    return json.dumps(payload)


def _hydrate_attr_from_payload(attr_cls, raw):
    """
    Build an in-memory Attribute from cached JSON (no PG read).
    """
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if raw == _MISSING_MARKER:
        return None
    try:
        data = json.loads(raw)
        pk = data.get("pk")
        if not pk:
            return None
        pickle_val = data.get("db_value")
        if pickle_val is not None:
            # Reverse of _encode_attr: dbsafe_decode returns the live Python object,
            # matching what PickledObjectField.from_db_value produces on an ORM load.
            pickle_val = dbsafe_decode(pickle_val)
        attr = attr_cls(
            pk=pk,
            db_key=data.get("db_key"),
            db_category=data.get("db_category"),
            db_model=data.get("db_model"),
            db_attrtype=data.get("db_attrtype"),
            db_lock_storage=data.get("db_lock_storage") or "",
            db_val_type=data.get("db_val_type") or "",
            db_int_val=data.get("db_int_val"),
            db_float_val=data.get("db_float_val"),
            db_str_val=data.get("db_str_val"),
            db_strvalue=data.get("db_strvalue"),
            db_value=pickle_val,
        )
        attr.id = pk
        attr._state.adding = False
        return attr
    except Exception:
        logger.log_trace("redis_attr_cache._hydrate_attr_from_payload")
        return None


def _decode_redis_keys(keys):
    return [k.decode() if isinstance(k, bytes) else k for k in keys]


class _AttrConn:
    """Minimal through-row stand-in for cache hits."""

    def __init__(self, attribute):
        self.attribute = attribute


class RedisCachedModelAttributeBackend(ModelAttributeBackend):
    """
    ModelAttributeBackend with Redis L2 in front of PG reads.

    Write ordering: updates mark the attr dirty (deferred PG write) without
    touching Redis. Redis is republished only by ``flush_dirty`` after
    ``bulk_update`` succeeds, enforcing the invariant that Redis is never
    more current than PG. This avoids a phantom-data window where a crash
    between cache publish and PG flush would leave Redis serving values
    PG never saw. Deletes drop Redis immediately (delete is a synchronous
    PG op, not write-behind).

    Same-process readers still see new values after ``do_update_attribute``
    via the AttributeHandler's in-process cache (the mutated attr object
    is the same instance). Cross-process readers see stale Redis values
    until the next ``flush_all_dirty`` tick, which is the same window as
    PG durability.
    """

    def _cache_set(self, key, category, attr, *, mark_missing=False, nx_only=False):
        """Publish an attribute (or missing-marker) to Redis.

        Args:
            nx_only: If True, only write the value when the key doesn't already
                exist (``SET ... NX``). Used by post-PG-read cache fills so a
                concurrent writer's newer value isn't overwritten with the
                stale snapshot we just pulled from PG (TOCTOU between PG read
                and Redis publish).
        """
        r = _redis_conn()
        if not r:
            return
        try:
            rkey = _redis_key(self._model, self._objid, key, category)
            if mark_missing:
                payload = _MISSING_MARKER
            elif attr and attr.pk:
                payload = _encode_attr(attr)
            else:
                return
            if nx_only:
                # SET NX: only writes if the key is currently absent. If a
                # concurrent do_update_attribute already published a newer
                # value, this returns None and we leave Redis alone.
                if not r.set(rkey, payload, ex=_ttl(), nx=True):
                    return
                # Index membership is fine to add unconditionally.
                pipe = r.pipeline()
            else:
                pipe = r.pipeline()
                pipe.set(rkey, payload, ex=_ttl())
            pipe.sadd(_obj_index_key(self._model, self._objid), rkey)
            pipe.expire(_obj_index_key(self._model, self._objid), _ttl())
            cat_key = _category_index_key(self._model, self._objid, category)
            pipe.sadd(cat_key, rkey)
            pipe.expire(cat_key, _ttl())
            pipe.execute()
        except Exception:
            logger.log_trace("redis_attr_cache._cache_set")

    def _cache_drop(self, key, category):
        r = _redis_conn()
        if not r:
            return
        try:
            rkey = _redis_key(self._model, self._objid, key, category)
            pipe = r.pipeline()
            pipe.delete(rkey)
            pipe.srem(_obj_index_key(self._model, self._objid), rkey)
            pipe.srem(_category_index_key(self._model, self._objid, category), rkey)
            pipe.execute()
        except Exception:
            logger.log_trace("redis_attr_cache._cache_drop")

    def _cache_drop_object(self):
        r = _redis_conn()
        if not r:
            return
        try:
            index_key = _obj_index_key(self._model, self._objid)
            keys = _decode_redis_keys(list(r.smembers(index_key) or []))
            to_delete = list(keys)
            to_delete.append(index_key)
            # drop category indexes (pattern scan is expensive; track via object index only)
            if keys:
                r.delete(*to_delete)
            else:
                r.delete(index_key)
        except Exception:
            logger.log_trace("redis_attr_cache._cache_drop_object")

    def _index_populated(self, r) -> bool:
        try:
            return bool(r.exists(_obj_index_key(self._model, self._objid)))
        except Exception:
            return False

    def _attrs_from_redis_keys(self, r, keys) -> List:
        decoded = list(_decode_redis_keys(keys))
        if not decoded:
            return []
        try:
            # Batch all key fetches into a single MGET pipeline round-trip.
            raws = r.mget(decoded)
        except Exception:
            return []
        attrs = []
        for raw in raws:
            if raw is None:
                continue
            attr = _hydrate_attr_from_payload(self._attrclass, raw)
            if attr:
                attrs.append(attr)
        return attrs

    def _warm_from_pg_list(self, attrs):
        for attr in attrs:
            self._cache_set(attr.db_key, attr.db_category, attr)

    def query_key(self, key, category):
        if not _enabled() or not self.obj.pk:
            return super().query_key(key, category)

        r = _redis_conn()
        if r:
            try:
                raw = r.get(_redis_key(self._model, self._objid, key, category))
                if raw is not None:
                    if raw == _MISSING_MARKER or (
                        isinstance(raw, bytes) and raw.decode() == _MISSING_MARKER
                    ):
                        return []
                    attr = _hydrate_attr_from_payload(self._attrclass, raw)
                    if attr:
                        return [_AttrConn(attr)]
                    return []
            except Exception:
                logger.log_trace("redis_attr_cache.query_key")

        conn = super().query_key(key, category)
        # Use NX semantics: if a concurrent writer published a newer value
        # to Redis between our miss above and this fill, leave it alone.
        if conn:
            self._cache_set(key, category, conn[0].attribute, nx_only=True)
        else:
            self._cache_set(key, category, None, mark_missing=True, nx_only=True)
        return conn

    def query_category(self, category):
        if not _enabled() or not self.obj.pk:
            return super().query_category(category)

        r = _redis_conn()
        if r and self._index_populated(r):
            try:
                keys = r.smembers(_category_index_key(self._model, self._objid, category))
                if keys is not None:
                    return self._attrs_from_redis_keys(r, keys)
            except Exception:
                logger.log_trace("redis_attr_cache.query_category")

        attrs = super().query_category(category)
        self._warm_from_pg_list(attrs)
        return attrs

    def query_all(self):
        if not _enabled() or not self.obj.pk:
            return super().query_all()

        r = _redis_conn()
        if r and self._index_populated(r):
            try:
                keys = r.smembers(_obj_index_key(self._model, self._objid))
                if keys is not None:
                    return self._attrs_from_redis_keys(r, keys)
            except Exception:
                logger.log_trace("redis_attr_cache.query_all")

        attrs = super().query_all()
        self._warm_from_pg_list(attrs)
        return attrs

    def do_create_attribute(self, key, category, lockstring, value, strvalue):
        attr = super().do_create_attribute(key, category, lockstring, value, strvalue)
        self._cache_set(key, category, attr)
        return attr

    def do_delete_attribute(self, attr):
        key, category = attr.db_key, attr.db_category
        super().do_delete_attribute(attr)
        self._cache_drop(key, category)

    def flush_dirty(self):
        # super().flush_dirty() returns the exact list it wrote. If the
        # bulk_update raised, it re-raises before reaching us, so we
        # never publish unflushed values to Redis.
        flushed = super().flush_dirty() or ()
        if not _enabled():
            return flushed
        for attr in flushed:
            if attr and attr.pk:
                self._cache_set(attr.db_key, attr.db_category, attr)
        return flushed


# Maps Attribute.db_model values to (app_label, model_name) for through-table
# lookups. Owner classes all live under TypedObject and expose db_attributes
# M2M; this lets invalidate_attrs() map orphan-flushed Attributes back to the
# owner pk(s) needed to compose Redis keys.
_OWNER_MODEL_LOOKUP = {
    "objectdb": ("objects", "ObjectDB"),
    "accountdb": ("accounts", "AccountDB"),
    "scriptdb": ("scripts", "ScriptDB"),
    "channeldb": ("comms", "ChannelDB"),
}


def invalidate_attrs(attrs) -> None:
    """Drop Redis cache entries for the given Attributes.

    Called by the orphan-flush path after a successful ``bulk_update`` so a
    direct ``attr.value = X`` write (which bypasses backend.flush_dirty)
    still invalidates Redis. Without this, cross-process readers would
    serve stale cached values until TTL expiry even though PG was updated.

    Best-effort: silent no-op when Redis is disabled, unavailable, or a
    lookup fails. The next read repopulates Redis from PG.
    """
    if not _enabled() or not attrs:
        return
    r = _redis_conn()
    if not r:
        return
    try:
        from django.apps import apps

        by_model: dict = {}
        for attr in attrs:
            if not attr or not getattr(attr, "pk", None):
                continue
            model = (getattr(attr, "db_model", "") or "").lower()
            if model in _OWNER_MODEL_LOOKUP:
                by_model.setdefault(model, []).append(attr)
        keys_to_delete = []
        for model, model_attrs in by_model.items():
            app_label, class_name = _OWNER_MODEL_LOOKUP[model]
            Owner = apps.get_model(app_label, class_name)
            attr_by_pk = {a.pk: a for a in model_attrs}
            through = Owner.db_attributes.through
            owner_fk = f"{model}_id"
            for owner_id, attr_id in through.objects.filter(
                attribute_id__in=list(attr_by_pk.keys())
            ).values_list(owner_fk, "attribute_id"):
                attr = attr_by_pk.get(attr_id)
                if attr:
                    keys_to_delete.append(
                        _redis_key(model, owner_id, attr.db_key, attr.db_category)
                    )
        if keys_to_delete:
            r.delete(*keys_to_delete)
    except Exception:
        logger.log_trace("redis_attr_cache.invalidate_attrs")


def flush_all_keys() -> int:
    """Drop every ``attr:<version>:*`` key from the Redis L2 cache.

    Wired into ``evennia.utils.idmapper.models.flush_cache`` so test
    ``tearDown``, the ``@reload/flush`` admin command, and the
    ``post_migrate`` signal all leave Redis in a clean state — without
    this, CI runs (where Redis persists across tests) saw stale cache
    hits leaking from a previous test's writes, while local runs
    without Redis enabled never tripped the issue.

    No-op when ``ATTRIBUTE_REDIS_CACHE_ENABLED`` is off or the Redis
    connection is unavailable, so callers (including the idmapper
    flush) can invoke unconditionally.

    Uses ``SCAN`` rather than ``KEYS`` so the call stays non-blocking
    on large keyspaces (the prefix scope keeps the scan tight to this
    cache's own keys).

    Returns:
        int: Number of Redis keys deleted, or 0 on no-op / failure.
    """
    if not _enabled():
        return 0
    r = _redis_conn()
    if not r:
        return 0
    deleted = 0
    try:
        # Match every attribute-cache key (attr:<version>:*) plus the
        # per-object and per-category index keys (same prefix).
        pattern = f"attr:{_CACHE_VERSION}:*"
        batch = []
        for key in r.scan_iter(match=pattern, count=500):
            batch.append(key)
            if len(batch) >= 500:
                deleted += r.delete(*batch)
                batch.clear()
        if batch:
            deleted += r.delete(*batch)
    except Exception:
        logger.log_trace("redis_attr_cache.flush_all_keys")
    return deleted
