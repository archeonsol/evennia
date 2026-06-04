"""
Default no-match emote lines (`.` / `,` prefixes).

When the action engine handles input, lines that start with ``.`` or ``,`` and
match no registered verb are treated as poses (legacy CmdNoMatch behavior).
"""

from __future__ import annotations

from evennia.narrative.delivery import default_emote_delivery

from ..parser import NoMatchAction
from ..result import CLAIM, SKIP
from ..rule import rule

__all__ = ["DefaultEmoteNoMatchRules"]


class DefaultEmoteNoMatchRules:
    """``carry_out`` rules for dot/comma pose lines on ``NoMatchAction``."""

    @rule(NoMatchAction, phase="carry_out", priority=300)
    def nomatch_emote(self, action, actor):
        char = actor.character
        if char is None or self is not char:
            return SKIP
        raw = (action.raw_string or "").strip()
        if raw.startswith("."):
            emote_text = raw[1:].strip()
            if emote_text:
                default_emote_delivery.run(char, emote_text, msg_type="pose")
                return CLAIM
        if raw.startswith(",") and len(raw) > 1:
            default_emote_delivery.run(char, raw, msg_type="pose")
            return CLAIM
        return SKIP
