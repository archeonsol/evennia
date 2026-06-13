"""
Default movement (CM1): the engine-shipped traversal action, base traverse rules,
the sustained :class:`Locomotion` activity, and the dynamic exit-name resolver.

This is the action-engine equivalent of Evennia's default ``CmdLook``/``CmdGet`` in
``evennia/commands/default/`` — the *generic* movement substrate every game gets
out of the box, the same way the cmdset world ships ``DefaultExit.at_traverse``:

* :class:`Move` — a typed, system-verb (``__move__``) traversal attempt. Never
  parsed from raw input; only produced by :func:`exit_resolver` (a bare/typed exit
  name) or re-dispatched by :class:`Locomotion`.
* :class:`ExitTraversalRules` — the baseline ``check`` (destination exists, the
  exit's ``traverse`` lock) + ``carry_out`` (hand a player-initiated move to a
  :class:`Locomotion`, else one ``move_to``). Mirrors ``ExitCommand.func`` 1:1.
* :class:`CharacterMovementRules` — the ``report`` rule that emits
  :class:`~evennia.actions.default.events.Departed` / ``Arrived``.
* :class:`Locomotion` — a sustained, interruptible, **re-validating** route
  follower (generalizes the legacy ``staggered_movement`` contrib): it re-resolves
  the exit and re-dispatches a fresh :class:`Move` per step, so a door slammed
  mid-walk stops the walk with no separate recheck path. Pacing/narration are
  overridable hooks (:meth:`Locomotion.step_delay`, :meth:`Locomotion.announce`,
  :meth:`Locomotion.resolve_step`) so a game tunes the feel without touching the
  mechanism.
* :func:`exit_resolver` — the concrete :class:`~evennia.actions.parser.DynamicVerbResolver`
  that turns a per-room exit name into a :class:`Move` (the engine ships the
  protocol; this is its default implementation).

A game adds its own movement gates (posture, combat, locked doors, faction
clearance, …) purely as extra ``@rule(Move, phase="check")`` providers on its
Character/Exit typeclasses — never by redefining :class:`Move`. Rules compose
across providers; the action type is shared. The game's typed block reasons live
in its own enum and are recorded via :meth:`Move.block`; the engine only knows
``block_reason`` is an integer mask.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..action import Action, GameObject, action
from ..context import build_context
from ..engine import engine
from ..process import Activity
from ..result import CLAIM, FAIL, PASS, SKIP
from ..rule import rule
from .events import Arrived, Departed

__all__ = [
    "Move",
    "Locomotion",
    "ExitTraversalRules",
    "CharacterMovementRules",
    "exit_resolver",
    "register_exit_resolver",
]


# --------------------------------------------------------------------------- #
# The action
# --------------------------------------------------------------------------- #


@action("__move__")
@dataclass
class Move(Action):
    """Traverse one exit once. A *system* action (verb ``__move__`` is excluded
    from the trie): never parsed from raw input — only produced by
    :func:`exit_resolver` or re-dispatched by :class:`Locomotion`.

    Attributes:
        exit (GameObject): the resolved exit to traverse (a dispatch target, so it
            becomes a rule provider — the source of any exit-side gates).
        direction (str): canonical direction string for messages/events.
        sneak (bool): stealthed step — passed to ``move_to`` and the events.
        quiet (bool): suppress default arrive/depart messaging (a Locomotion may
            drive its own narration).
        staggered (bool): True for a player-initiated move (bare exit / ``go``):
            once it clears the gates, ``carry_out`` hands off to a sustained
            :class:`Locomotion` instead of moving instantly. False for the moves
            the Locomotion itself re-dispatches each step — those are the
            instantaneous ``move_to``. (Re-validation == re-dispatch.)
        block_reason (int): inherited from :class:`~evennia.actions.action.Action`;
            game ``check`` rules set typed flags via :meth:`block` (e.g. ``MoveBlock``).
    """

    # __primary_handler__ = Exit  # set by the game at production wiring
    exit: GameObject = None
    direction: str = ""
    sneak: bool = False
    quiet: bool = False
    staggered: bool = True

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        # System verb: never reached from raw input. A trivial parse keeps the
        # registry/contract consistent.
        return cls()


# --------------------------------------------------------------------------- #
# Exit-name resolution (the DynamicVerbResolver default)
# --------------------------------------------------------------------------- #


def _exits_in(location):
    from evennia.objects.objects import DefaultExit

    contents = getattr(location, "contents", None) or []
    return [
        obj
        for obj in contents
        if isinstance(obj, DefaultExit) and getattr(obj, "destination", None)
    ]


def _exit_names(exit_obj):
    names = [(exit_obj.key or "").strip().lower()]
    aliases = getattr(exit_obj, "aliases", None)
    if aliases is not None and hasattr(aliases, "all"):
        try:
            names.extend(str(a).strip().lower() for a in aliases.all())
        except Exception:
            pass
    return [n for n in names if n]


def exit_resolver(stripped, actor):
    """Resolve a stripped input line to a :class:`Move` if it names a local exit.

    Consulted by ``ActionParser.parse`` only after the trie and symbol-prefix
    lookups both miss (so a real verb always wins) and before ``_nomatch``.
    Returns ``None`` (decline) when there is no location, no exits, or no match;
    raises :class:`~evennia.actions.exceptions.AmbiguousTarget` when several exits
    answer to the name (the dispatch bridge turns that into a disambiguation
    prompt — the same contract as ``actor.search``).
    """
    from ..exceptions import AmbiguousTarget

    location = getattr(actor, "location", None)
    if location is None:
        return None
    exits = _exits_in(location)
    if not exits:
        return None

    token = stripped.strip().lower()
    matches = [ex for ex in exits if token in _exit_names(ex)]
    if not matches:
        first = token.split(" ", 1)[0]
        if first and first != token:
            matches = [ex for ex in exits if first in _exit_names(ex)]
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousTarget(matches, stripped)

    exit_obj = matches[0]
    return Move(exit=exit_obj, direction=(exit_obj.key or "away").strip())


def register_exit_resolver(parser=None):
    """Register :func:`exit_resolver` on the shared (or given) parser. Idempotent."""
    from ..parser import parser as _shared_parser

    (parser or _shared_parser).add_resolver(exit_resolver)
    return exit_resolver


# --------------------------------------------------------------------------- #
# Locomotion — the sustained route follower
# --------------------------------------------------------------------------- #


class Locomotion(Activity):
    """Walk a route of directions, one re-validated step at a time.

    Where :class:`Move` is the instantaneous attempt, ``Locomotion`` is the
    *process*: it re-resolves the exit in the mover's *current* room each step
    (the room changes as we walk, which is why the route is directions, not a
    fixed exit list), waits :meth:`step_delay`, then re-dispatches a fresh
    ``Move(staggered=False)``. Because every step re-runs the full ``check``
    phase, a gate that closes mid-walk stops the route — re-validation *is*
    re-dispatch.

    Subclass hooks (override to flavor the walk without touching control flow):
        * :meth:`step_delay` — seconds to pace before each step (default: none).
        * :meth:`announce` — per-step departure narration / cost (default: none).
        * :meth:`resolve_step` — map a direction to an exit (default: match the
          exit ``key``/``aliases`` in the current room).

    Args:
        actor (Actor): the mover's actor view.
        route (iterable[str]): direction tokens to walk, in order.
        sneak (bool): walk stealthed — passed through to each ``Move``.
    """

    key = "locomotion"
    exclusive_group = "locomotion"
    #: default pacing (seconds) between steps; a game subclass overrides
    #: :meth:`step_delay` (or this constant) to add RP cadence.
    step_delay_seconds = 0.0

    def __init__(self, actor, route, sneak: bool = False):
        super().__init__(actor=actor)
        self.route = list(route)
        self.sneak = sneak

    def step_delay(self, char) -> float:
        return self.step_delay_seconds

    def announce(self, char, direction):
        return None

    def resolve_step(self, char, direction):
        loc = getattr(char, "location", None)
        if loc is None:
            return None, None
        token = (direction or "").strip().lower()
        for ex in _exits_in(loc):
            if token in _exit_names(ex):
                return ex, getattr(ex, "destination", None)
        return None, None

    def run(self):
        actor = self.actor
        for direction in self.route:
            char = getattr(actor, "character", None)
            if char is None:
                return
            exit_obj, dest = self.resolve_step(char, direction)
            if not exit_obj or not dest:
                char.msg(f"There is no exit {direction}.")
                return

            display = (exit_obj.key or direction).strip()
            self.announce(char, display)

            delay = self.step_delay(char)
            if delay:
                yield delay
            if self.cancelled:
                return

            context = build_context(actor, targets=[exit_obj])
            trace = yield engine.dispatch(
                Move(
                    exit=exit_obj,
                    direction=display,
                    sneak=self.sneak,
                    quiet=self.sneak,
                    staggered=False,  # this IS the step — move now, don't re-stagger
                ),
                actor,
                context,
            )
            if getattr(trace, "outcome", None) == "blocked":
                # A gate refused mid-walk (door closed, combat started, …). The
                # check rule already messaged the actor; stop the route.
                self.cancel(reason=getattr(trace, "block_message", None))
                return


# --------------------------------------------------------------------------- #
# Base rule providers
# --------------------------------------------------------------------------- #


class ExitTraversalRules:
    """Baseline exit-side rules, mixed into the Exit typeclass.

    The generic traversal semantics: the destination must exist and the exit's
    own ``traverse`` lock must pass (mirroring ``ExitCommand``'s
    ``access(caller, "traverse")``), then ``carry_out`` either hands a
    player-initiated (staggered) move to a :attr:`locomotion_class` walk or
    performs the single ``move_to``. A game adds further gates as extra
    ``check`` rules on its own Exit subclass; it sets :attr:`locomotion_class`
    to its :class:`Locomotion` subclass to flavor pacing/narration.

    Every rule guards ``action.exit is self`` so only the exit actually being
    traversed responds (other exits in the room are providers too).
    """

    #: the Locomotion subclass a staggered move hands off to. Override on the
    #: game's Exit mixin to inject RP pacing/narration.
    locomotion_class = Locomotion

    def _is_target(self, action) -> bool:
        return action.exit is self

    @rule(Move, phase="check", priority=70)
    def check_destination(self, action, actor):
        if not self._is_target(action):
            return SKIP
        if not getattr(self, "destination", None):
            return action.block(0, "You can't go that way.")
        return PASS

    @rule(Move, phase="check", priority=68)
    def check_traverse_lock(self, action, actor):
        if not self._is_target(action):
            return SKIP
        caller = actor.character
        if caller is None:
            return SKIP
        if hasattr(self, "access") and not self.access(caller, "traverse"):
            err = getattr(getattr(self, "db", None), "err_traverse", None)
            return action.block(0, err or "You cannot go that way.")
        return PASS

    @rule(Move, phase="carry_out")
    def carry_out_move(self, action, actor):
        """Hand a staggered move to a :attr:`locomotion_class` walk, or perform
        the one ``move_to`` (a Locomotion's own per-step re-dispatch). The
        ``check`` phase already passed every gate."""
        if not self._is_target(action):
            return SKIP
        caller = actor.character
        if caller is None:
            return SKIP
        dest = getattr(self, "destination", None)
        if dest is None:
            return CLAIM

        if action.staggered:
            direction = action.direction or (self.key or "away").strip()
            actor.start_activity(self.locomotion_class(actor, [direction], sneak=action.sneak))
            return CLAIM

        action._origin = getattr(caller, "location", None)
        moved = caller.move_to(dest, quiet=action.quiet, move_type="traverse")
        action._moved = bool(moved)
        return CLAIM


class CharacterMovementRules:
    """Baseline mover-side rule, mixed into the Character typeclass.

    Emits :class:`Departed`/:class:`Arrived` once the mover has actually moved.
    Fired from the mover's frame (a single provider, so it fires once); the
    exit's ``carry_out`` stashed ``_origin``/``_moved`` on the action. A game
    adds its own movement gates (posture, combat, …) as extra ``check`` rules.
    """

    def _is_mover(self, actor) -> bool:
        return self is getattr(actor, "character", None)

    @rule(Move, phase="report")
    def report_departed(self, action, actor):
        if not self._is_mover(actor):
            return SKIP
        if not getattr(action, "_moved", False):
            return SKIP
        origin = getattr(action, "_origin", None)
        dest = getattr(self, "location", None)
        kwargs = dict(
            mover=self,
            origin=origin,
            destination=dest,
            direction=action.direction,
            exit=action.exit,
            sneak=action.sneak,
        )
        engine.emit(Departed(**kwargs))
        engine.emit(Arrived(**kwargs))
        return PASS
