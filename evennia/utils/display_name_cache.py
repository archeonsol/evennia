"""
Viewer-aware display name cache for ``msg_contents`` and related paths.
"""

from __future__ import annotations

import time

from django.conf import settings


def _enabled() -> bool:
    return bool(getattr(settings, "MSG_DISPLAY_NAME_CACHE_ENABLED", True))


def _ttl() -> float:
    return float(getattr(settings, "MSG_DISPLAY_NAME_CACHE_TTL", 300) or 0)


def _cache(looker):
    if not looker or not hasattr(looker, "ndb"):
        return None
    store = getattr(looker.ndb, "_display_name_cache", None)
    if store is None:
        store = {}
        looker.ndb._display_name_cache = store
    return store


def invalidate_display_name_cache(looker) -> None:
    """Clear cached names for a looker (e.g. after recognizer changes)."""
    if looker and hasattr(looker, "ndb"):
        try:
            del looker.ndb._display_name_cache
        except AttributeError:
            looker.ndb._display_name_cache = {}


def _perception_gen(obj, looker) -> tuple:
    """Generations included in cache keys so bumps invalidate without scanning ndb."""
    og = (
        int(getattr(getattr(looker, "ndb", None), "_recog_generation", 0) or 0)
        if looker
        else 0
    )
    sg = (
        int(getattr(getattr(obj, "ndb", None), "_sdesc_generation", 0) or 0)
        if obj
        else 0
    )
    return (og, sg)


def bump_recog_generation(viewer) -> None:
    """Call when a viewer's recog / helmet-recog / disguise data changes."""
    if viewer and hasattr(viewer, "ndb"):
        ndb = viewer.ndb
        ndb._recog_generation = int(getattr(ndb, "_recog_generation", 0) or 0) + 1


def bump_sdesc_generation(character) -> None:
    """Call when a character's visible short description inputs change."""
    if character and hasattr(character, "ndb"):
        ndb = character.ndb
        ndb._sdesc_generation = int(getattr(ndb, "_sdesc_generation", 0) or 0) + 1


def cached_get_display_name(obj, looker, **kwargs):
    """
    ``obj.get_display_name(looker=looker, **kwargs)`` with per-looker ndb cache.
    """
    if not _enabled() or not looker or not hasattr(obj, "get_display_name"):
        if hasattr(obj, "get_display_name"):
            return obj.get_display_name(looker=looker, **kwargs)
        return str(obj)

    store = _cache(looker)
    if store is None:
        return obj.get_display_name(looker=looker, **kwargs)

    ttl = _ttl()
    key = (
        id(obj),
        id(looker),
        _perception_gen(obj, looker),
        tuple(sorted(kwargs.items())) if kwargs else (),
    )
    now = time.time()
    entry = store.get(key)
    if entry and (ttl <= 0 or (now - entry[1]) < ttl):
        return entry[0]

    name = obj.get_display_name(looker=looker, **kwargs)
    store[key] = (name, now)
    return name
