"""
Default roleplay actions (emote tiers B3): pose and emote.

Uses :class:`~evennia.narrative.delivery.DefaultEmoteDelivery` with
:class:`~evennia.narrative.protocols.KeyNameResolver` for key-based targeting.
"""

from __future__ import annotations

from dataclasses import dataclass

from evennia.narrative.delivery import default_emote_delivery

from ..action import Action, action
from ..result import CLAIM, PASS, SKIP
from ..rule import rule

__all__ = [
    "Pose",
    "Emote",
    "DefaultRoleplayRules",
]


@action("pose", ".", ",")
@dataclass
class Pose(Action):
    """First-person roleplay pose (room sees third person)."""

    text: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        text = (raw_args or "").strip()
        if verb == ",":
            text = "," + text
        return cls(text=text)


@action("emote")
@dataclass
class Emote(Action):
    """Simple emote: name plus literal third-person text."""

    text: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        return cls(text=(raw_args or "").strip())


class DefaultRoleplayRules:
    """Baseline ``pose`` / ``emote`` carry_out via narrative delivery."""

    def _is_actor(self, actor) -> bool:
        return self is getattr(actor, "character", None)

    @rule(Pose, phase="check", priority=90)
    def check_pose(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        text = (action.text or "").strip()
        if not text or not text.lstrip(".,").strip():
            actor.character.msg("Usage: . <first-person text>")
            return CLAIM
        if not getattr(actor.character, "location", None):
            return CLAIM
        return PASS

    @rule(Emote, phase="check", priority=90)
    def check_emote(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        if not action.text:
            actor.character.msg(
                "Usage: emote <text>  (e.g. emote waves his hand at Bob)"
            )
            return CLAIM
        if not getattr(actor.character, "location", None):
            return CLAIM
        return PASS

    @rule(Pose, phase="carry_out")
    def carry_out_pose(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        default_emote_delivery.run(
            actor.character,
            action.text,
            msg_type="pose",
        )
        return CLAIM

    @rule(Emote, phase="carry_out")
    def carry_out_emote(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        default_emote_delivery.run(
            actor.character,
            action.text,
            msg_type="emote",
            literal_third=True,
        )
        return CLAIM
