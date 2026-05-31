"""
Redis-backed channel subscriber index for fast online fan-out.

PostgreSQL M2M subscriptions remain source of truth; Redis is a denormalized
cache used for ``Channel.msg`` recipient gathering. Disable via
``CHANNEL_SUBSCRIBER_CACHE_ENABLED = False``.

Cache contract:

- **Fills on:**

  - ``sync_channel_subscribers(channel)`` — full rebuild from DB inside a
    MULTI/EXEC ``DELETE + SADD`` so a concurrent ``add_subscriber`` /
    ``remove_subscriber`` between the DB read and cache rewrite is not
    silently lost. Triggered on the first ``get_cached_subscribers`` lookup
    against a channel with no Redis key, and again when a stale ref's
    owner is missing from PG.
  - ``add_subscriber(channel, entity)`` — incremental SADD on subscribe.

- **Invalidates on:**

  - ``remove_subscriber(channel, entity)`` — incremental SREM on unsubscribe.
  - ``clear_channel(channel)`` — explicit drop.
  - Implicit: a ``get_cached_subscribers`` call that resolves a ref whose
    owner no longer exists triggers ``sync_channel_subscribers`` to rebuild
    the set from PG truth.

- **Staleness bound:** zero for in-process subscribe / unsubscribe (the
  SADD/SREM lands before the call returns). Cross-process subscribe /
  unsubscribe is visible at the next ``sync_channel_subscribers`` rebuild;
  references to deleted entities are filtered on resolve, so visible
  staleness is bounded by the detect-and-resync round-trip.
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
    return getattr(settings, "CHANNEL_SUBSCRIBER_CACHE_ALIAS", "default")


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


def remove_subscriber_from_all_channels(entity) -> None:
    """Drop ``entity``'s subscriber ref from every channel's Redis index.

    Called from a ``pre_delete`` receiver on AccountDB and ObjectDB so a
    deleted entity does not leak stale ``a:<pk>`` / ``o:<pk>`` refs into
    every channel it subscribed to. Without this, each channel only
    self-heals on its next fan-out (``get_cached_subscribers`` trips
    ``ObjectDoesNotExist`` and triggers a single-channel resync), so
    stale refs accumulate in proportion to subscription count times
    death rate.

    Best-effort: silent no-op when the cache is disabled, Redis is
    unavailable, the entity has no subscriber ref (untyped instance),
    or the subscription read fails.

    Args:
        entity: The AccountDB or ObjectDB instance being deleted. Read
            via the ``account_subscription_set`` / ``object_subscription_set``
            reverse managers, which are still intact at ``pre_delete`` time.
    """
    if not _enabled() or entity is None:
        return
    ref = _member_ref(entity)
    if not ref:
        return
    r = _redis_conn()
    if not r:
        return
    channels = []
    try:
        account_subs = getattr(entity, "account_subscription_set", None)
        object_subs = getattr(entity, "object_subscription_set", None)
        if account_subs is not None:
            channels.extend(account_subs.all())
        if object_subs is not None:
            channels.extend(object_subs.all())
    except Exception:
        logger.log_trace("channel_subscriber_cache: subscription read failed")
        return
    if not channels:
        return
    try:
        pipe = r.pipeline()
        for channel in channels:
            pipe.srem(_channel_key(channel.id), ref)
        pipe.execute()
    except Exception:
        logger.log_trace("channel_subscriber_cache: bulk remove failed")


def _resolve_refs(refs: Iterable[str]) -> List:
    """Resolve a batch of ``kind:pk`` refs to model instances in one query per kind.

    Stale refs (pk no longer in DB) are silently skipped, matching the prior
    per-ref ``DoesNotExist`` swallow. Input order is preserved.
    """
    from evennia.accounts.models import AccountDB
    from evennia.objects.models import ObjectDB

    parsed = []
    account_pks = []
    object_pks = []
    for ref in refs:
        kind, pk = _parse_ref(ref)
        if not pk:
            continue
        parsed.append((kind, pk))
        if kind == "a":
            account_pks.append(pk)
        elif kind == "o":
            object_pks.append(pk)

    accounts = {}
    objects = {}
    if account_pks:
        try:
            accounts = {a.pk: a for a in AccountDB.objects.filter(pk__in=account_pks)}
        except Exception:
            logger.log_trace("channel_subscriber_cache: account bulk-resolve failed")
    if object_pks:
        try:
            objects = {o.pk: o for o in ObjectDB.objects.filter(pk__in=object_pks)}
        except Exception:
            logger.log_trace("channel_subscriber_cache: object bulk-resolve failed")

    out = []
    for kind, pk in parsed:
        obj = accounts.get(pk) if kind == "a" else objects.get(pk) if kind == "o" else None
        if obj is not None:
            out.append(obj)
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
            try:
                from evennia.server.prometheus_metrics import \
                    record_channel_subscriber_cache_miss

                record_channel_subscriber_cache_miss()
            except Exception:
                pass
        else:
            try:
                from evennia.server.prometheus_metrics import \
                    record_channel_subscriber_cache_hit

                record_channel_subscriber_cache_hit()
            except Exception:
                pass
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
