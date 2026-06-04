"""
Default movement events (CM1): typed :class:`~evennia.actions.events.Event`\\s
emitted in the ``report`` phase of a completed :class:`Move`.

These ship with the engine because they are game-agnostic: a movement spans two
rooms, and the destination room is **not** in the original dispatch context —
which is exactly why an :class:`~evennia.actions.events.Event` owns its own
provider scope. :meth:`Moved.providers` returns the mover plus both rooms and
their contents, so ``engine.emit`` reaches subscribers on either side (a guard
watching who leaves, a follower tagging along, a trap arming on arrival) without
the engine ever hard-coding "rooms".

A game layers reactive movement (follow/escort, aggro pulls, ambient triggers) by
``@subscribe``-ing these on its own typeclasses; it need not redefine the events.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..events import Event

__all__ = ["Moved", "Departed", "Arrived"]


@dataclass
class Moved(Event):
    """Base movement event. Carries the mover, both rooms, and the direction.

    Attributes:
        mover: the character that moved.
        origin: the room departed (may be ``None`` for a spawn/teleport).
        destination: the room arrived in.
        direction (str): the canonical direction string ("north", "up", …).
        exit: the exit traversed (``None`` if moved without an exit, e.g.
            teleport).
        sneak (bool): whether the move was a stealthed step — used by subscribers
            to suppress overt reactions.
    """

    mover: object = None
    origin: object = None
    destination: object = None
    direction: str = ""
    exit: object = None
    sneak: bool = False

    def providers(self):
        objs = [self.mover, self.origin, self.destination]
        for room in (self.origin, self.destination):
            contents = getattr(room, "contents", None) if room is not None else None
            if contents:
                objs.extend(contents)
        return objs


@dataclass
class Departed(Moved):
    """Emitted from the *origin* room's frame: the mover has just left.

    Subscribers in the origin room (and the mover's followers, who live there
    until they react) handle this — e.g. followers queue their own step.
    """


@dataclass
class Arrived(Moved):
    """Emitted from the *destination* room's frame: the mover has just entered.

    Subscribers in the destination handle reception — aggro pulls, room
    on-arrival hooks, tracking.
    """
