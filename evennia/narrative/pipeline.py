"""The named render/deliver core: register producers once, call by ``kind``.

This is the formal top of the R1 pipeline. Two entry points:

- :func:`render` ``(obj, viewer, *, kind, **ctx)`` dispatches to the producer
  registered for ``kind`` and returns its result -- a
  :class:`~evennia.narrative.rendernode.RenderNode` (or, for a not-yet-migrated
  surface, a flattened string).
- :func:`~evennia.narrative.rendernode.deliver_node` is the delivery core
  (re-exported here): resolve/flatten per the viewer's client capability and
  send. It keeps its own name to stay distinct from
  :meth:`~evennia.narrative.delivery.DefaultEmoteDelivery.deliver`, which takes
  an ``EmotePlan``.

Together with the :class:`~evennia.narrative.render.PipelineResolver` pass runner,
this turns per-viewer output into an *engine mechanism*: a game registers its
producers and passes once, rather than every call site wiring naming, perception,
psychosis and language by hand. Surfaces adopt the named core one at a time
(strangler); registering a producer does not force its call sites to route
through :func:`render` until they are migrated.
"""

from __future__ import annotations

from evennia.narrative.rendernode import deliver_node

__all__ = ["register_producer", "get_producer", "producers", "render", "deliver_node"]

_PRODUCERS = {}


def register_producer(kind, fn=None, *, override=False):
    """Register a producer for ``kind``. Usable as a decorator or a direct call.

    Registration is expected to complete at startup, before any :func:`render`
    dispatch. Re-registering an existing ``kind`` raises rather than silently
    shadowing the earlier producer, so an accidental double-registration is
    caught; pass ``override=True`` to replace one deliberately.

    Args:
        kind (str): the producer key.
        fn (callable, optional): the producer. Omit to use as a decorator.
        override (bool): allow replacing an already-registered ``kind``.

    Raises:
        ValueError: if ``kind`` is already registered and ``override`` is False.
    """

    def _set(f):
        if kind in _PRODUCERS and not override:
            raise ValueError(
                f"a producer is already registered for kind {kind!r}; "
                "pass override=True to replace it"
            )
        _PRODUCERS[kind] = f
        return f

    if fn is None:
        return _set
    return _set(fn)


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
