"""
Per-caller cache for ``Command.access(caller, "cmd")`` during command parsing.

Enabled with ``CMD_ACCESS_CACHE_ENABLED`` in settings. Results are stored on
``caller.ndb`` and keyed by a generation counter plus command identity and
optional session id (when lockfuncs use the session).

Invalidate automatically when cmdsets change on the owning object (and related
objects such as room contents or exit locations). Call
``invalidate_cmd_access_cache(caller)`` when permissions or locks change without
a cmdset update.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from django.conf import settings

_CACHE_ATTR = "_cmd_access_cache"
_GEN_ATTR = "_cmd_access_cache_gen"


def _enabled() -> bool:
    return bool(getattr(settings, "CMD_ACCESS_CACHE_ENABLED", False))


def _generation(caller) -> int:
    if not caller or not hasattr(caller, "ndb"):
        return 0
    gen = getattr(caller.ndb, _GEN_ATTR, None)
    if gen is None:
        gen = 0
        caller.ndb._cmd_access_cache_gen = gen
    return int(gen)


def _bump_generation(caller) -> None:
    if not caller or not hasattr(caller, "ndb"):
        return
    caller.ndb._cmd_access_cache_gen = _generation(caller) + 1


def _cache_dict(caller) -> Optional[Dict[Tuple, bool]]:
    if not caller or not hasattr(caller, "ndb"):
        return None
    cache = getattr(caller.ndb, _CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(caller.ndb, _CACHE_ATTR, cache)
    return cache


def invalidate_cmd_access_cache(caller) -> None:
    """
    Drop cached cmd access results for ``caller`` (next check recomputes).
    """
    if not caller or not hasattr(caller, "ndb"):
        return
    _bump_generation(caller)
    try:
        delattr(caller.ndb, _CACHE_ATTR)
    except AttributeError:
        pass


def _cmd_identity(cmd) -> Tuple:
    """Stable key for a command class/instance used in parser matching."""
    path = getattr(cmd, "path", None)
    if path:
        return ("path", path)
    cls = type(cmd)
    return ("class", cls.__module__, cls.__name__, getattr(cmd, "key", None))


def _lookup_key(cmd, caller, session=None) -> Tuple:
    # session.sessid is the stable per-connection identifier; using id()
    # risked aliasing if a Session was GC'd and a new one landed at the
    # same address while the caller's ndb cache outlived it.
    sess_id = getattr(session, "sessid", None) if session is not None else None
    return (_generation(caller), _cmd_identity(cmd), sess_id)


def cached_cmd_access(cmd, caller, session=None) -> bool:
    """
    Return whether ``caller`` may run ``cmd``, using ndb cache when enabled.
    """
    if not _enabled():
        return cmd.access(caller, "cmd", session=session)

    cache = _cache_dict(caller)
    if cache is None:
        return cmd.access(caller, "cmd", session=session)

    key = _lookup_key(cmd, caller, session=session)
    if key in cache:
        try:
            from evennia.server.prometheus_metrics import \
                record_cmd_access_cache_hit

            record_cmd_access_cache_hit()
        except Exception:
            pass
        return cache[key]

    allowed = cmd.access(caller, "cmd", session=session)
    cache[key] = allowed
    try:
        from evennia.server.prometheus_metrics import \
            record_cmd_access_cache_miss

        record_cmd_access_cache_miss()
    except Exception:
        pass
    return allowed


def invalidate_for_cmdset_owner(obj) -> None:
    """
    Invalidate access caches affected when ``obj``'s cmdset stack changes.
    """
    if not _enabled() or obj is None:
        return

    invalidate_cmd_access_cache(obj)

    # Exit cmdsets are merged from location contents — clear everyone in the room.
    if getattr(obj, "destination", None) is not None:
        loc = getattr(obj, "location", None)
        if loc and hasattr(loc, "contents_get"):
            for content in loc.contents_get(exclude=obj):
                invalidate_cmd_access_cache(content)
        return

    # Room-like containers: contents inherit merged cmdsets from the room.
    if hasattr(obj, "contents_get") and not getattr(obj, "has_account", False):
        for content in obj.contents_get():
            invalidate_cmd_access_cache(content)
        return

    # Account cmdset changes affect puppeted characters.
    characters = getattr(obj, "characters", None)
    if characters is not None:
        try:
            for char in characters.all():
                invalidate_cmd_access_cache(char)
        except Exception:
            pass
