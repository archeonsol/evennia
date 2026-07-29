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
        normalized = str(key or "").strip().lower()
        if not normalized:
            raise ValueError("render transform key is required")
        if normalized in _TRANSFORMS and not override:
            raise ValueError(f"render transform already registered: {normalized}")
        _TRANSFORMS[normalized] = (int(priority), transform)
        return transform

    if fn is None:
        return _set
    return _set(fn)


def unregister_transform(key):
    """Remove and return one registered transform."""
    entry = _TRANSFORMS.pop(str(key or "").strip().lower(), None)
    return entry[1] if entry else None


def transforms():
    """Return transforms in deterministic execution order."""
    return tuple(
        (key, fn)
        for key, (_priority, fn) in sorted(
            _TRANSFORMS.items(), key=lambda item: (item[1][0], item[0])
        )
    )
