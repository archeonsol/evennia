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
    """
    Increment generation on ``obj`` and its location (room cmdset sources).
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
    return hit


def set_cached_location_cmdsets(key: tuple, cmdsets: list) -> None:
    if not _enabled():
        return
    _CACHE[key] = cmdsets
    _CACHE.move_to_end(key)
    maxsize = _maxsize()
    while len(_CACHE) > maxsize:
        _CACHE.popitem(last=False)
