"""
Redis-backed channel subscriber index for fast online fan-out.

PostgreSQL M2M subscriptions remain source of truth; Redis is a denormalized cache
rebuilt on subscribe/unsubscribe and used for ``Channel.msg`` recipient gathering.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

from django.conf import settings

from evennia.utils import logger

_CACHE_VERSION = "v1"
_KEY_PREFIX = "chsubs:%s:" % _CACHE_VERSION


def _enabled() -> bool:
    return bool(getattr(settings, "CHANNEL_SUBSCRIBER_CACHE_ENABLED", True))


def _redis_alias() -> str:
    return getattr(settings, "CHANNEL_SUBSCRIBER_CACHE_REDIS_ALIAS", "default")


def _redis_conn():
    try:
        from django_redis import get_redis_connection

        return get_redis_connection(_redis_alias())
    except Exception as exc:
        logger.log_warn("channel_subscriber_cache: redis unavailable: %s" % exc)
        return None


def _channel_key(channel_id: int) -> str:
    return _KEY_PREFIX + str(int(channel_id))


def _member_ref(entity) -> Optional[str]:
    if entity is None:
        return None
    clsname = getattr(getattr(entity, "__dbclass__", None), "__name__", "")
    pk = getattr(entity, "id", None) or getattr(entity, "pk", None)
    if pk is None:
        return None
    if clsname == "AccountDB":
        return "a:%s" % pk
    if clsname == "ObjectDB":
        return "o:%s" % pk
    return None


def _parse_ref(ref: str):
    if not ref or ":" not in ref:
        return None, None
    kind, _, pk = ref.partition(":")
    try:
        return kind, int(pk)
    except (TypeError, ValueError):
        return None, None


def sync_channel_subscribers(channel) -> None:
    """Rebuild Redis set from DB subscriptions.

    The DELETE + SADD pair runs inside a MULTI/EXEC transaction so a
    concurrent add_subscriber / remove_subscriber landing between the
    DB read and the cache rewrite is not silently overwritten. Without
    ``transaction=True`` django_redis's pipeline is a simple batched
    request and another client's SADD can be wiped by our DELETE.
    """
    if not _enabled() or channel is None:
        return
    r = _redis_conn()
    if not r:
        return
    key = _channel_key(channel.id)
    try:
        refs = set()
        for sub in list(channel.db_account_subscriptions.all()) + list(
            channel.db_object_subscriptions.all()
        ):
            ref = _member_ref(sub)
            if ref:
                refs.add(ref)
        pipe = r.pipeline(transaction=True)
        pipe.delete(key)
        if refs:
            pipe.sadd(key, *sorted(refs))
        pipe.execute()
    except Exception:
        logger.log_trace("channel_subscriber_cache: sync failed")


def add_subscriber(channel, entity) -> None:
    if not _enabled() or channel is None:
        return
    ref = _member_ref(entity)
    if not ref:
        return
    r = _redis_conn()
    if not r:
        return
    try:
        r.sadd(_channel_key(channel.id), ref)
    except Exception:
        logger.log_trace("channel_subscriber_cache: add failed")


def remove_subscriber(channel, entity) -> None:
    if not _enabled() or channel is None:
        return
    ref = _member_ref(entity)
    if not ref:
        return
    r = _redis_conn()
    if not r:
        return
    try:
        r.srem(_channel_key(channel.id), ref)
    except Exception:
        logger.log_trace("channel_subscriber_cache: remove failed")


def clear_channel(channel) -> None:
    if not _enabled() or channel is None:
        return
    r = _redis_conn()
    if not r:
        return
    try:
        r.delete(_channel_key(channel.id))
    except Exception:
        logger.log_trace("channel_subscriber_cache: clear failed")


def _resolve_refs(refs: Iterable[str]) -> List:
    from evennia.accounts.models import AccountDB
    from evennia.objects.models import ObjectDB

    out = []
    for ref in refs:
        kind, pk = _parse_ref(ref)
        if not pk:
            continue
        try:
            if kind == "a":
                obj = AccountDB.objects.get(id=pk)
            elif kind == "o":
                obj = ObjectDB.objects.get(id=pk)
            else:
                continue
            out.append(obj)
        except Exception:
            continue
    return out


def get_cached_subscribers(channel, *, online_only: bool = True) -> Optional[List]:
    """
    Return subscriber objects from Redis index, or None if cache miss/disabled.
    """
    if not _enabled() or channel is None:
        return None
    r = _redis_conn()
    if not r:
        return None
    try:
        if not r.exists(_channel_key(channel.id)):
            sync_channel_subscribers(channel)
        refs = r.smembers(_channel_key(channel.id))
        if not refs:
            return []
        if isinstance(refs, set):
            ref_list = sorted((x.decode("utf-8") if isinstance(x, bytes) else str(x) for x in refs))
        else:
            ref_list = list(refs)
        subs = _resolve_refs(ref_list)
        if not online_only:
            return subs
        online = []
        from django.core.exceptions import ObjectDoesNotExist

        for obj in subs:
            try:
                if obj.is_connected:
                    online.append(obj)
            except ObjectDoesNotExist:
                sync_channel_subscribers(channel)
                return None
        return online
    except Exception:
        logger.log_trace("channel_subscriber_cache: get failed")
        return None
