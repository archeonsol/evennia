"""
Redis L2 cache for ModelAttributeBackend (Phase 2).

PostgreSQL remains source of truth. On Redis failure, all operations fall back to PG.
"""

from __future__ import annotations

import base64
import json
import time

from django.conf import settings

from evennia.typeclasses.attributes import ModelAttributeBackend
from evennia.utils import logger
from evennia.utils.utils import to_str

_CACHE_VERSION = "v1"
_LOG_INTERVAL = 60.0
_last_redis_warn = 0.0


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


def _encode_attr(attr):
    payload = {
        "pk": attr.pk,
        "db_key": attr.db_key,
        "db_category": attr.db_category,
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


def _hydrate_attr(attr_cls, raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
        pk = data.get("pk")
        if not pk:
            return None
        attr = attr_cls.objects.get(pk=pk)
        return attr
    except Exception:
        return None


class _AttrConn:
    """Minimal through-row stand-in for cache hits."""

    def __init__(self, attribute):
        self.attribute = attribute


class RedisCachedModelAttributeBackend(ModelAttributeBackend):
    """
    ModelAttributeBackend with Redis L2 in front of PG reads.
    Writes go through parent (write-behind to PG); Redis keys updated or dropped.
    """

    def _cache_set(self, key, category, attr):
        r = _redis_conn()
        if not r or not attr or not attr.pk:
            return
        try:
            pipe = r.pipeline()
            pipe.set(_redis_key(self._model, self._objid, key, category), _encode_attr(attr), ex=_ttl())
            pipe.sadd(_obj_index_key(self._model, self._objid), _redis_key(self._model, self._objid, key, category))
            pipe.expire(_obj_index_key(self._model, self._objid), _ttl())
            pipe.execute()
        except Exception:
            logger.log_trace("redis_attr_cache._cache_set")

    def _cache_drop(self, key, category):
        r = _redis_conn()
        if not r:
            return
        try:
            rkey = _redis_key(self._model, self._objid, key, category)
            r.delete(rkey)
            r.srem(_obj_index_key(self._model, self._objid), rkey)
        except Exception:
            logger.log_trace("redis_attr_cache._cache_drop")

    def _cache_drop_object(self):
        r = _redis_conn()
        if not r:
            return
        try:
            index_key = _obj_index_key(self._model, self._objid)
            keys = list(r.smembers(index_key) or [])
            if keys:
                decoded = [k.decode() if isinstance(k, bytes) else k for k in keys]
                r.delete(*decoded, index_key)
            else:
                r.delete(index_key)
        except Exception:
            logger.log_trace("redis_attr_cache._cache_drop_object")

    def query_key(self, key, category):
        if not _enabled() or not self.obj.pk:
            return super().query_key(key, category)

        r = _redis_conn()
        if r:
            try:
                raw = r.get(_redis_key(self._model, self._objid, key, category))
                attr = _hydrate_attr(self._attrclass, raw)
                if attr:
                    return [_AttrConn(attr)]
                if raw is not None:
                    return []
            except Exception:
                logger.log_trace("redis_attr_cache.query_key")

        conn = super().query_key(key, category)
        if conn:
            self._cache_set(key, category, conn[0].attribute)
        return conn

    def query_category(self, category):
        if not _enabled():
            return super().query_category(category)
        return super().query_category(category)

    def query_all(self):
        if not _enabled():
            return super().query_all()
        return super().query_all()

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
