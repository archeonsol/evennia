"""
Redis L2 cache for ModelAttributeBackend (Phase 2).

PostgreSQL remains source of truth. On Redis failure, all operations fall back to PG.
Cache hits hydrate Attribute instances from JSON without a database round-trip.
"""

from __future__ import annotations

import base64
import json
import time
from typing import List, Optional

from django.conf import settings

from evennia.typeclasses.attributes import ModelAttributeBackend
from evennia.utils import logger
from evennia.utils.utils import to_str

_CACHE_VERSION = "v1"
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
        payload["db_value"] = base64.b64encode(attr.db_value).decode("ascii")
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
            pickle_val = base64.b64decode(pickle_val)
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
    Writes go through parent (write-behind to PG); Redis keys updated or dropped.
    """

    def _cache_set(self, key, category, attr, *, mark_missing=False):
        r = _redis_conn()
        if not r:
            return
        try:
            rkey = _redis_key(self._model, self._objid, key, category)
            pipe = r.pipeline()
            if mark_missing:
                pipe.set(rkey, _MISSING_MARKER, ex=_ttl())
            elif attr and attr.pk:
                pipe.set(rkey, _encode_attr(attr), ex=_ttl())
            else:
                return
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
        attrs = []
        for rkey in _decode_redis_keys(keys):
            try:
                raw = r.get(rkey)
            except Exception:
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
        if conn:
            self._cache_set(key, category, conn[0].attribute)
        else:
            self._cache_set(key, category, None, mark_missing=True)
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

    def do_update_attribute(self, attr, value, strvalue):
        super().do_update_attribute(attr, value, strvalue)
        self._cache_set(attr.db_key, attr.db_category, attr)

    def do_delete_attribute(self, attr):
        key, category = attr.db_key, attr.db_category
        super().do_delete_attribute(attr)
        self._cache_drop(key, category)

    def flush_dirty(self):
        refresh = list(self._dirty_attrs) if _enabled() else []
        super().flush_dirty()
        for attr in refresh:
            if attr and attr.pk:
                self._cache_set(attr.db_key, attr.db_category, attr)
