"""
Default roleplay actions: say, whisper, pose and emote.

Uses :class:`~evennia.narrative.delivery.DefaultEmoteDelivery` with
:class:`~evennia.narrative.protocols.KeyNameResolver` for key-based targeting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag

from evennia.narrative.delivery import default_emote_delivery
from evennia.objects.character import DefaultCharacter
from evennia.utils.utils import resolve_transform

from ..action import Action, action
from ..result import CLAIM, PASS, SKIP
from ..rule import rule

__all__ = [
    "Say",
    "Whisper",
    "Pose",
    "Emote",
    "RoleplayBlock",
    "DefaultRoleplayRules",
]


class RoleplayBlock(IntFlag):
    """Typed reasons a default roleplay action can be refused."""

    EMPTY = 1
    NO_LOCATION = 2
    NO_RECEIVERS = 4


@action("say", '"', "'")
@dataclass
class Say(Action):
    """Speak aloud in the acting character's location.

    |wsay <message>|n / |w\"<message>|n / |w'<message>|n. A nonempty message
    and a location are required; normal speech hooks transform and deliver it.
    """

    __primary_handler__ = DefaultCharacter

    text: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        """Keep speech as free text, including text adjacent to quote aliases."""
        return cls(text=(raw_args or "").strip())


@action("whisper")
@dataclass
class Whisper(Action):
    """Speak privately to one or more locally resolved receivers.

    |wwhisper <receiver>[, <receiver> ...] = <message>|n. A nonempty message,
    location, and at least one resolved receiver are required; recipients are
    deduplicated and receive the speech through normal whisper hooks.
    """

    __primary_handler__ = DefaultCharacter

    receivers: tuple = ()
    message: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        """Parse ``receiver[, receiver ...] = message`` and resolve locally.

        Resolution is ordered and identity-deduplicated. Failed searches are
        omitted (the search API owns any not-found feedback), and every
        successful receiver is exposed by :attr:`targets` for provider scope.
        """
        raw = (raw_args or "").strip()
        lhs, sep, rhs = raw.partition("=")
        if not sep:
            return cls()
        resolved = []
        seen = set()
        for query in (part.strip() for part in lhs.split(",")):
            if not query:
                continue
            receiver = actor.search(query)
            marker = id(receiver)
            if receiver is not None and marker not in seen:
                seen.add(marker)
                resolved.append(receiver)
        return cls(receivers=tuple(resolved), message=rhs.strip())

    @property
    def targets(self):
        """Return every resolved receiver in parse order."""
        return list(self.receivers)


@action("pose", ".", ",")
@dataclass
class Pose(Action):
    """Send a first-person roleplay pose to the current room.

    |wpose <text>|n / |w. <text>|n / |w, <continuation>|n. A nonempty pose and
    location are required; normal emote delivery renders it for observers.
    """

    __primary_handler__ = DefaultCharacter

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
    """Send a third-person emote to the current room.

    |wemote <text>|n. A nonempty message and location are required; normal
    emote delivery resolves and renders the caller name.
    """

    __primary_handler__ = DefaultCharacter

    text: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        return cls(text=(raw_args or "").strip())


class DefaultRoleplayRules:
    """Baseline rules for the canonical roleplay action family."""

    def _is_actor(self, actor) -> bool:
        return self is getattr(actor, "character", None)

    @rule(Say, phase="check", priority=90)
    def check_say(self, action, actor):
        """Reject structurally empty or locationless speech without side effects."""
        if not self._is_actor(actor):
            return SKIP
        if not action.text:
            return action.block(RoleplayBlock.EMPTY, "Say what?")
        if not getattr(actor.character, "location", None):
            return action.block(RoleplayBlock.NO_LOCATION, "You have no location to speak in.")
        return PASS

    @rule(Whisper, phase="check", priority=90)
    def check_whisper(self, action, actor):
        """Reject malformed whispers without messaging from the check body."""
        if not self._is_actor(actor):
            return SKIP
        if not action.message:
            return action.block(RoleplayBlock.EMPTY, "Usage: whisper <character> = <message>")
        if not getattr(actor.character, "location", None):
            return action.block(RoleplayBlock.NO_LOCATION, "You have no location to whisper in.")
        if not action.receivers:
            return action.block(RoleplayBlock.NO_RECEIVERS, "No whisper receivers found.")
        return PASS

    @rule(Pose, phase="check", priority=90)
    def check_pose(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        text = (action.text or "").strip()
        if not text or not text.lstrip(".,").strip():
            return action.block(RoleplayBlock.EMPTY, "Usage: . <first-person text>")
        if not getattr(actor.character, "location", None):
            return action.block(RoleplayBlock.NO_LOCATION, "You have no location to pose in.")
        return PASS

    @rule(Emote, phase="check", priority=90)
    def check_emote(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        if not action.text:
            return action.block(
                RoleplayBlock.EMPTY,
                "Usage: emote <text>  (e.g. emote waves his hand at Bob)",
            )
        if not getattr(actor.character, "location", None):
            return action.block(RoleplayBlock.NO_LOCATION, "You have no location to emote in.")
        return PASS

    @rule(Say, phase="carry_out")
    def carry_out_say(self, action, actor):
        """Run the stable speech transform and delivery hooks."""
        if not self._is_actor(actor):
            return SKIP
        speech = resolve_transform(self.at_pre_say(action.text), action.text)
        if speech:
            self.at_say(speech, msg_self=True)
        return CLAIM

    @rule(Whisper, phase="carry_out")
    def carry_out_whisper(self, action, actor):
        """Run private speech hooks, preserving self-whisper semantics."""
        if not self._is_actor(actor):
            return SKIP
        receivers = list(action.receivers)
        speech = resolve_transform(
            self.at_pre_say(action.message, whisper=True, receivers=receivers),
            action.message,
        )
        if speech:
            self.at_say(
                speech,
                msg_self=None if self in receivers else True,
                receivers=receivers,
                whisper=True,
            )
        return CLAIM

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
