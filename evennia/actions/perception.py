"""
``perception`` (CM1): the engine-level visibility seam for target resolution.

Target resolution (:meth:`evennia.actions.actor.Actor.search`) must hide objects
the searcher cannot perceive — canonically a character hiding in stealth that
the searcher has not spotted. The legacy game wired that filter *inside* its own
``Character.search`` override: one chokepoint, but buried in a typeclass method,
invisible at call sites, and reachable only because every resolution path
happened to go through ``.search``.

This module lifts the *policy hook* into the engine. The game registers a single
visibility predicate once at startup; ``Actor.search`` applies it to both the
disambiguation candidate set and the resolved result. Target resolution is then
filtered in the substrate, independent of whatever the game's ``.search`` does —
which is what lets the ``Character.search`` override be retired later (Phase 8)
without regressing typed-action targeting.

Design contract:

* **Permissive default.** With no filter registered the engine is inert
  (everything visible), so importing the action system changes nothing until a
  game calls :func:`set_visibility_filter`.
* **Fail open.** A misbehaving predicate is treated as "visible". A perception
  bug must never make the world vanish or break resolution; over-revealing is
  the strictly safer failure than a search that silently returns nothing.
* **Pure policy.** The engine never inspects game state here; it only calls the
  registered predicate ``fn(searcher, candidate) -> bool``.
"""

__all__ = [
    "set_visibility_filter",
    "get_visibility_filter",
    "is_visible",
    "filter_visible",
]

_visibility_filter = None


def set_visibility_filter(fn):
    """Register the game's visibility predicate.

    Args:
        fn (callable | None): ``fn(searcher, candidate) -> bool``. ``searcher``
            is the acting object (the actor's ``effective``); ``candidate`` is a
            resolved world object. Return ``False`` to hide ``candidate`` from
            ``searcher``. Pass ``None`` to clear (restores the permissive
            default).
    """
    global _visibility_filter
    _visibility_filter = fn


def get_visibility_filter():
    """Return the currently registered predicate (or ``None`` if unset)."""
    return _visibility_filter


def is_visible(searcher, candidate) -> bool:
    """True if ``searcher`` can perceive ``candidate``.

    Permissive when no filter is registered, when ``candidate`` is falsy, or when
    the predicate raises (fail open).
    """
    fn = _visibility_filter
    if fn is None or not candidate:
        return True
    try:
        return bool(fn(searcher, candidate))
    except Exception:
        return True


def filter_visible(searcher, candidates):
    """Return the visible subset of ``candidates``, order preserved."""
    return [c for c in candidates if is_visible(searcher, c)]
