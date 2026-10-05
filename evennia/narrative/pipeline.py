"""Ordered universal delivery transforms.

Game surfaces construct canonical plans through their explicit plan builders.
This module contains only cross-surface transforms applied at resolution.
Delivery has one top in :mod:`evennia.narrative.plan`: ``deliver`` for one
viewer and ``deliver_to`` for an audience.
"""

from __future__ import annotations

from evennia.narrative.plan import RenderPlan, deliver, deliver_to, resolve, text_plan
from evennia.narrative.rendernode import deliver_node

__all__ = [
    "DROP_DELIVERY",
    "register_transform",
    "unregister_transform",
    "transforms",
    "deliver_node",
    "RenderPlan",
    "resolve",
    "deliver",
    "deliver_to",
    "text_plan",
]

_TRANSFORMS = {}
#: The registry in execution order, built when first asked for and dropped when
#: the registry changes. ``transforms()`` is called for every node for every
#: viewer, and sorting the registry each time was most of what it cost.
_ORDERED = None


class _DropDelivery:
    """Private sentinel returned by a transform to suppress one delivery."""

    __slots__ = ()


DROP_DELIVERY = _DropDelivery()


def register_transform(key, fn=None, *, priority=0, override=False):
    """Register an ordered universal node transform.

    Transforms receive ``(node, viewer, context)`` and must return a new
    :class:`RenderNode` or :data:`DROP_DELIVERY`. They are suitable for
    accessibility, perception, language, policy metadata, and other
    cross-surface concerns. A drop is private to this viewer; the canonical
    event has already been published.
    """

    def _set(transform):
        global _ORDERED
        normalized = str(key or "").strip().lower()
        if not normalized:
            raise ValueError("render transform key is required")
        if normalized in _TRANSFORMS and not override:
            raise ValueError(f"render transform already registered: {normalized}")
        _TRANSFORMS[normalized] = (int(priority), transform)
        _ORDERED = None
        return transform

    if fn is None:
        return _set
    return _set(fn)


def unregister_transform(key):
    """Remove and return one registered transform."""
    global _ORDERED
    entry = _TRANSFORMS.pop(str(key or "").strip().lower(), None)
    if entry is None:
        return None
    _ORDERED = None
    return entry[1]


def transforms():
    """Return transforms in deterministic execution order.

    The same tuple comes back until a transform is registered or removed.
    """
    global _ORDERED
    ordered = _ORDERED
    if ordered is None:
        ordered = _ORDERED = tuple(
            (key, fn)
            for key, (_priority, fn) in sorted(
                _TRANSFORMS.items(), key=lambda item: (item[1][0], item[0])
            )
        )
    return ordered
