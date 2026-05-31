"""
Per-caller cache for ``Command.access(caller, "cmd")`` during command parsing.

Enabled with ``CMD_ACCESS_CACHE_ENABLED`` in settings (default on). Commands
whose class overrides ``Command.access`` are bypassed automatically (see
``_command_uses_base_access``) because an override may consult runtime
state outside the cache key's reach. Results live on
``caller.ndb._cmd_access_cache`` so a reload clears the cache.

Cache contract:

- **Fills on:** first ``cached_cmd_access(cmd, caller, session)`` lookup for a
  given key, storing ``True``/``False``. Key components: per-caller generation
  counter, command identity (path when present, else module/class/key tuple),
  and ``session.sessid`` (``id()`` would alias after Session GC).
- **Invalidates on:**

  - ``invalidate_cmd_access_cache(caller)`` — explicit drop when permissions,
    tags, or locks on ``caller`` change without an associated cmdset update.
  - ``invalidate_for_cmdset_owner(obj)`` — fired by the cmdset-change signal.
    Invalidates ``obj`` directly, plus every co-resident object (for exit
    cmdsets merged from room contents and room-like containers) and every
    puppeted character (for account cmdset changes).

  Both paths bump the per-caller generation counter so prior keys become
  unreachable, then drop the cache dict.
- **Staleness bound:** zero after either invalidation. The cache is per-caller
  ``ndb`` so a reload also clears it.
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


_BASE_ACCESS = None


def _command_uses_base_access(cmd) -> bool:
    """Return True iff ``cmd`` uses the stock ``Command.access`` implementation.

    Auto-skip seam mirroring the ``"match" in type(cmd).__dict__`` check in
    :mod:`evennia.commands.cmdparser_trie`: a Command class that overrides
    ``access`` may consult runtime state outside the cache key's reach
    (time, randomness, ad-hoc DB queries), so caching its results would
    serve stale decisions. Base ``Command.access`` delegates straight to
    LockHandler; its result is pure-function-of-cached-state and safe to
    cache.
    """
    global _BASE_ACCESS
    if _BASE_ACCESS is None:
        from evennia.commands.command import Command

        _BASE_ACCESS = Command.access
    return type(cmd).access is _BASE_ACCESS


def cached_cmd_access(cmd, caller, session=None) -> bool:
    """
    Return whether ``caller`` may run ``cmd``, using ndb cache when enabled.
    """
    if not _enabled() or not _command_uses_base_access(cmd):
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


def invalidate_caller_access(*targets) -> None:
    """Invalidate all engine-owned per-caller access caches for ``targets``.

    Single seam for callers that mutate a target's effective permissions
    (``@perm``, ``@quell``, future engine paths). Fans out to every
    engine-owned cache that keys on per-caller access decisions, in the
    order they need to be invalidated for a subsequent access check to
    see fresh state. Today that is:

    - ``invalidate_cmd_access_cache(target)``
    - :func:`evennia.locks.lockhandler.invalidate_lock_cache(target)`

    Future engine-owned caller-access caches must be added here so
    callers keep using one seam instead of remembering an N-of-M
    fan-out. Call this *before* firing any signal whose subscribers
    may observe an access check, so subscribers see consistent state
    (the ``permissions_changed`` signal contract relies on this
    ordering).

    Args:
        *targets: Objects, Accounts, or other lock-evaluable entities
            whose access caches should be dropped. ``None`` entries are
            silently skipped so callers can pass an optional puppet
            without a guard (``invalidate_caller_access(account, puppet)``).
    """
    from evennia.locks.lockhandler import invalidate_lock_cache

    for target in targets:
        if target is None:
            continue
        invalidate_cmd_access_cache(target)
        invalidate_lock_cache(target)


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
