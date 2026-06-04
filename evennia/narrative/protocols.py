"""
Narrative extension protocols (emote tiers B1).

Games plug in richer name/target resolution (sdesc, recognition, skin tones) by
subclassing or replacing :class:`NameResolver`, and richer delivery (language
garbling, cameras, improv) by implementing :class:`EmoteDelivery`.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

__all__ = [
    "NameResolver",
    "KeyNameResolver",
    "EmoteDelivery",
]


@runtime_checkable
class NameResolver(Protocol):
    """Resolve how a character is named in emote text and targeting.

    Extension points:
    - :meth:`display_name` — per-viewer visible name (sdesc/recog in games).
    - :meth:`find_targets_in_text` — scan emote body for mentioned characters.
    """

    def display_name(self, obj, viewer) -> str:
        """Return the name *viewer* should see for *obj* in emote targeting."""
        ...

    def find_targets_in_text(self, text: str, character_list, emitter) -> list:
        """Return ``[(matched_substring, character), ...]`` found in *text*."""
        ...


class KeyNameResolver:
    """Default resolver: match other characters by ``obj.key`` (case-insensitive)."""

    def display_name(self, obj, viewer) -> str:
        return getattr(obj, "key", str(obj))

    def find_targets_in_text(self, text, character_list, emitter):
        targets = []
        if not text or not character_list:
            return targets
        seen_positions = set()

        def _overlaps(s, e):
            for a, b in seen_positions:
                if not (e <= a or s >= b):
                    return True
            return False

        specs = []
        for char in character_list:
            if char == emitter:
                continue
            name = (self.display_name(char, emitter) or "").strip()
            if name:
                specs.append((name, char))
        specs.sort(key=lambda t: -len(t[0]))

        flags = re.IGNORECASE | re.UNICODE
        for name, char in specs:
            esc = re.escape(name)
            pattern = r"(?<!\w)" + esc + r"(?!\w)"
            for m in re.finditer(pattern, text, flags):
                start, end = m.start(), m.end()
                if _overlaps(start, end):
                    continue
                seen_positions.add((start, end))
                targets.append((m.group(0), char))

        def pos_key(t):
            pos = text.lower().find(t[0].lower())
            return (pos, -len(t[0]))

        targets.sort(key=pos_key)
        return targets


@runtime_checkable
class EmoteDelivery(Protocol):
    """Build and deliver a pose/emote to room viewers.

    Extension points:
    - :meth:`build_plan` — emitter-invariant segment plans + echoes.
    - :meth:`deliver` — per-viewer messaging from a plan.
    - :meth:`run` — convenience entry (validate location, literal-third shortcut).
    """

    def build_plan(self, caller, text: str, *, msg_type: str = "pose", improvise: bool = False):
        """Return an :class:`~evennia.narrative.emote.EmotePlan` or ``None``."""
        ...

    def deliver(self, plan):
        """Send messages for *plan*; return :class:`~evennia.narrative.emote.EmoteResult`."""
        ...

    def run(
        self,
        caller,
        text: str,
        *,
        msg_type: str = "pose",
        improvise: bool = False,
        literal_third: bool = False,
    ):
        """Validate input, build, and deliver (or literal-third broadcast)."""
        ...
