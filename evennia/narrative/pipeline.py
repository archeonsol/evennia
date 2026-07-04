"""The named render/deliver core: register producers once, call by ``kind``.

This is the formal top of the R1 pipeline. Two entry points:

- :func:`render` ``(obj, viewer, *, kind, **ctx)`` dispatches to the producer
  registered for ``kind`` and returns its result -- a
  :class:`~evennia.narrative.rendernode.RenderNode` (or, for a not-yet-migrated
  surface, a flattened string).
- :func:`deliver` is the delivery core (re-exported
  :func:`~evennia.narrative.rendernode.deliver_node`): resolve/flatten per the
  viewer's client capability and send.

Together with the :class:`~evennia.narrative.render.PipelineResolver` pass runner,
this turns per-viewer output into an *engine mechanism*: a game registers its
producers and passes once, rather than every call site wiring naming, perception,
psychosis and language by hand. Surfaces adopt the named core one at a time
(strangler); registering a producer does not force its call sites to route
through :func:`render` until they are migrated.
"""

from __future__ import annotations

from evennia.narrative.rendernode import deliver_node as deliver

__all__ = ["register_producer", "get_producer", "producers", "render", "deliver"]

_PRODUCERS = {}


def register_producer(kind, fn=None):
    """Register a producer for ``kind``. Usable as a decorator or a direct call."""
    if fn is None:
        def _decorator(f):
            _PRODUCERS[kind] = f
            return f

        return _decorator
    _PRODUCERS[kind] = fn
    return fn


def get_producer(kind):
    """Return the producer registered for ``kind``, or ``None``."""
    return _PRODUCERS.get(kind)


def producers():
    """Return a copy of the ``{kind: producer}`` registry (introspection/tests)."""
    return dict(_PRODUCERS)


def render(obj, viewer, *, kind, **ctx):
    """Dispatch to the producer for ``kind``: ``producer(obj, viewer, **ctx)``."""
    fn = _PRODUCERS.get(kind)
    if fn is None:
        raise LookupError(f"no render producer registered for kind {kind!r}")
    return fn(obj, viewer, **ctx)
