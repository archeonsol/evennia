"""
Cache location-sourced cmdsets gathered during command merge.

Invalidated via ``_cmdset_generation`` counters bumped on cmdset stack changes.
"""

from __future__ import annotations

from collections import OrderedDict

from django.conf import settings

_CACHE: OrderedDict = OrderedDict()


def _enabled() -> bool:
    return bool(getattr(settings, "LOCATION_CMDSET_CACHE_ENABLED", True))


def _maxsize() -> int:
    return int(getattr(settings, "LOCATION_CMDSET_CACHE_MAXSIZE", 512) or 512)


def cmdset_generation(obj) -> int:
    if not obj or not hasattr(obj, "ndb"):
        return 0
    return int(getattr(obj.ndb, "_cmdset_generation", 0) or 0)


def bump_cmdset_generation(obj) -> None:
    """Invalidate location-cmdset cache entries keyed off ``obj``.

    Increments the ``_cmdset_generation`` counter on ``obj`` and (if any)
    its location. ``make_cache_key`` mixes both counters into every cache
    key, so any future lookup for the same caller/location pair misses
    after a bump.

    **Invalidation contract.** Callers must fire this hook whenever a
    change could affect which cmdsets a future cmd-merge would resolve
    for objects sharing ``obj``'s location:

    - cmdset stack mutations on ``obj`` itself (add/remove/replace on
      its `CmdSetHandler`).
    - movement into or out of a location: the source room loses an
      exit/inhabitant, the destination gains one. The engine's default
      move primitive does this at both endpoints.

    The guarantees in exchange:

    - any cached merge result keyed against the *old* generation will be
      bypassed on subsequent lookups.
    - cached entries are not eagerly evicted; stale rows remain until
      the LRU evicts them or the cache is explicitly cleared. Memory is
      bounded by ``LOCATION_CMDSET_CACHE_MAXSIZE``.

    **Intended consumers.** Any game that pairs viewer-aware display
    names or per-caller permission filtering with cached cmdset
    lookups. The fork-side ``underspire`` game is today's only known
    consumer, but the engine ships the seam so downstream consumers
    don't have to monkey-patch the merge path to keep their cache
    coherent.

    Args:
        obj: The object whose cmdset stack changed. Ignored when falsy
            or lacking an ``ndb`` attribute (covers the bootstrap window
            before a typeclass is fully attached).

    """
    if not obj or not hasattr(obj, "ndb"):
        return
    obj.ndb._cmdset_generation = cmdset_generation(obj) + 1
    loc = getattr(obj, "location", None)
    if loc and hasattr(loc, "ndb"):
        loc.ndb._cmdset_generation = cmdset_generation(loc) + 1


def make_cache_key(caller, location) -> tuple:
    return (
        getattr(location, "pk", None) or id(location),
        cmdset_generation(location),
        getattr(caller, "pk", None) or id(caller),
        cmdset_generation(caller),
    )


def get_cached_location_cmdsets(key: tuple):
    if not _enabled():
        return None
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        try:
            from evennia.server.prometheus_metrics import \
                record_location_cmdset_cache_hit

            record_location_cmdset_cache_hit()
        except Exception:
            pass
    else:
        try:
            from evennia.server.prometheus_metrics import \
                record_location_cmdset_cache_miss

            record_location_cmdset_cache_miss()
        except Exception:
            pass
    return hit


def set_cached_location_cmdsets(key: tuple, cmdsets: list) -> None:
    if not _enabled():
        return
    _CACHE[key] = cmdsets
    _CACHE.move_to_end(key)
    maxsize = _maxsize()
    while len(_CACHE) > maxsize:
        _CACHE.popitem(last=False)


def clear_location_cmdset_cache() -> None:
    """Drop every cached entry. Intended for test teardown."""
    _CACHE.clear()
