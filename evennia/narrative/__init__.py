"""
Narrative utilities (pose/emote linguistics and delivery).

Extension points live in :mod:`evennia.narrative.protocols`; the default
delivery path is :class:`~evennia.narrative.delivery.DefaultEmoteDelivery`.
"""

from .delivery import DefaultEmoteDelivery
from .emote import (
    EmotePlan,
    EmoteResult,
    SegmentPlan,
    build_emote_segment_plans,
    first_to_second,
    first_to_third,
    split_emote_segments,
)
from .protocols import EmoteDelivery, KeyNameResolver, NameResolver
from .rendernode import RenderNode, deliver_node

__all__ = [
    "NameResolver",
    "KeyNameResolver",
    "EmoteDelivery",
    "DefaultEmoteDelivery",
    "RenderNode",
    "deliver_node",
    "EmotePlan",
    "EmoteResult",
    "SegmentPlan",
    "first_to_second",
    "first_to_third",
    "split_emote_segments",
    "build_emote_segment_plans",
]
