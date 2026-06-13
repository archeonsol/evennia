"""
Default object manipulation (CM1): engine-shipped get/drop/give/put/enter actions
and their baseline rule providers.

The action-engine analogue of ``evennia/commands/default/general.py``'s
``CmdGet``/``CmdDrop``/``CmdGive`` — plus the stock put/enter patterns games
commonly layer on — generic pickup/drop/transfer/insert/enter substrate every game
gets out of the box:

* :class:`Get` / :class:`Drop` / :class:`Give` — character-orchestrated verbs
  that carry parsed target specs (and optional stack counts) and resolve world
  objects in rules, matching :class:`~evennia.commands.default.general.CmdGet`'s
  room/inventory search + ``get`` lock + ``at_pre_*`` hooks + ``move_to``.
* :class:`Put` — parse-time item + container resolution; the container is the
  :attr:`~evennia.actions.action.Action.__primary_handler__` and owns the
  canonical ``carry_out`` (``move_to`` + ``at_pre_arrive``/``at_post_arrive``).
* :class:`Enter` — parse-time target; the enterable object owns ``carry_out``
  (calls ``at_enter``).

A game adds gates (corpse looting, cash piles, enterable mixins, …) as extra
``@rule`` providers on its typeclasses — never by redefining the action types.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evennia.objects.character import DefaultCharacter
from evennia.objects.object import DefaultObject

from ..action import Action, GameObject, action
from ..result import CLAIM, PASS, SKIP
from ..rule import rule

__all__ = [
    "Enterable",
    "Get",
    "Drop",
    "Give",
    "Put",
    "Enter",
    "CharacterObjectRules",
    "ContainerPutRules",
    "EnterableObjectRules",
    "_split_count",
]


class Enterable:
    """Marker for typeclasses mixed with enter rules (``at_enter``)."""


def _split_count(spec):
    """Extract a leading decimal count from a target spec.

    Mirrors Evennia's ``NumberedTargetCommand.parse``: ``"3 coins"`` →
    ``(3, "coins")``; ``"coins"`` → ``(0, "coins")``. Only the first token is
    consumed as a count when it is a bare decimal *and* more text follows.
    """
    count, *rest = (spec or "").split(maxsplit=1)
    if rest and count.isdecimal():
        return int(count), rest[0]
    return 0, (spec or "")


# --------------------------------------------------------------------------- #
# Action types
# --------------------------------------------------------------------------- #


@action("get", "take")
@dataclass
class Get(Action):
    """Pick up object(s) from the room (``get <obj>`` / ``get 3 coins``).

    Carries parsed strings; room search runs in rules so stacked pickup and
    game-specific branches (``get <item> from <container>``, cash piles, …)
    can layer via higher-priority providers. ``mode="from"`` is parsed but not
    handled by the engine baseline — games supply that ``carry_out``.
    """

    __primary_handler__ = DefaultCharacter
    mode: str = "plain"  # "bare" | "plain" | "from"
    count: int = 0
    obj_spec: str = ""
    container_spec: str = ""
    _usage: bool = field(default=False, init=False, repr=False, compare=False)

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        name = (raw_args or "").strip()
        if not name:
            return cls(mode="bare")
        count, rest = _split_count(name)
        if " from " in rest:
            item_spec, _, container_spec = rest.partition(" from ")
            item_spec = item_spec.strip()
            container_spec = container_spec.strip()
            if not item_spec or not container_spec:
                act = cls(mode="from")
                act._usage = True
                return act
            return cls(mode="from", count=count, obj_spec=item_spec, container_spec=container_spec)
        return cls(mode="plain", count=count, obj_spec=rest)


@action("drop")
@dataclass
class Drop(Action):
    """Drop object(s) from inventory into the current room (stacked-aware)."""

    __primary_handler__ = DefaultCharacter
    mode: str = "plain"  # "bare" | "plain"
    count: int = 0
    obj_spec: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        name = (raw_args or "").strip()
        if not name:
            return cls(mode="bare")
        count, rest = _split_count(name)
        return cls(mode="plain", count=count, obj_spec=rest)


@action("give")
@dataclass
class Give(Action):
    """Give inventory object(s) to another character (``= <target>`` or `` to ``)."""

    __primary_handler__ = DefaultCharacter
    mode: str = "plain"  # "plain" | "usage"
    count: int = 0
    item_spec: str = ""
    target_spec: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        raw = (raw_args or "").strip()
        item_spec = target_spec = ""
        if "=" in raw:
            item_spec, _, target_spec = raw.partition("=")
        elif " to " in raw:
            item_spec, _, target_spec = raw.partition(" to ")
        item_spec = item_spec.strip()
        target_spec = target_spec.strip()
        if not item_spec or not target_spec:
            return cls(mode="usage")
        count, item_spec = _split_count(item_spec)
        return cls(mode="plain", count=count, item_spec=item_spec, target_spec=target_spec)


@action("put", "insert")
@dataclass
class Put(Action):
    """Put an inventory object into a container in the room."""

    __primary_handler__ = DefaultObject
    target: GameObject = None
    container: GameObject = None
    _unresolved: bool = field(default=False, init=False, repr=False, compare=False)
    _usage: bool = field(default=False, init=False, repr=False, compare=False)

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        raw = (raw_args or "").strip()
        sep = " in " if " in " in raw else (" into " if " into " in raw else None)
        if sep is None:
            act = cls()
            act._usage = True
            return act
        item_spec, _, container_spec = raw.partition(sep)
        item_spec = item_spec.strip()
        container_spec = container_spec.strip()
        if not item_spec or not container_spec:
            act = cls()
            act._usage = True
            return act
        item = actor.search(item_spec, location=actor.character)
        container = actor.search(container_spec, location=actor.location)
        act = cls(target=item, container=container)
        if item is None or container is None:
            act._unresolved = True
        return act


@action("enter", "ride", "board")
@dataclass
class Enter(Action):
    """Enter a vehicle, arena, or any other enterable object."""

    __primary_handler__ = Enterable
    target: GameObject = None
    _unresolved: bool = field(default=False, init=False, repr=False, compare=False)

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        name = (raw_args or "").strip()
        if not name:
            return cls()
        found = actor.search(name, location=actor.location)
        act = cls(target=found)
        if found is None:
            act._unresolved = True
        return act


# --------------------------------------------------------------------------- #
# Character-side rules (get / drop / give + put/enter preflight)
# --------------------------------------------------------------------------- #


class CharacterObjectRules:
    """Baseline mover-side rules, mixed into the Character typeclass.

    Every rule guards ``self is actor.character`` so a bystander character in
    the room (also a provider) abstains. ``check`` holds lock/veto predicates;
    ``carry_out`` performs ``move_to`` and room messages for get/drop/give.
    """

    def _is_actor(self, actor) -> bool:
        return self is getattr(actor, "character", None)

    # --- get -----------------------------------------------------------------

    @rule(Get, phase="check", priority=90)
    def check_get_plain(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        if action.mode in ("bare", "from") or action._usage:
            return PASS
        caller = self
        args = action.obj_spec
        if not args:
            return action.block(0, "Get what?")
        from evennia.utils import utils

        objs = caller.search(args, location=caller.location, stacked=action.count)
        if not objs:
            return CLAIM
        objs = utils.make_iter(objs)
        if len(objs) == 1 and caller == objs[0]:
            return action.block(0, "You can't get yourself.")
        if all(obj.location == caller for obj in objs):
            if len(objs) == 1:
                return action.block(
                    0, f"You are already carrying {objs[0].get_display_name(caller)}."
                )
            return action.block(0, "You are already carrying those.")
        from evennia.utils.utils import is_veto

        for obj in objs:
            if not obj.access(caller, "get"):
                err = getattr(getattr(obj, "db", None), "get_err_msg", None)
                return action.block(0, err or "You can't get that.")
            if is_veto(obj.at_pre_get(caller)):
                return CLAIM
        action._get_objs = objs
        return PASS

    @rule(Get, phase="carry_out", priority=50)
    def carry_out_get(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if action.mode == "bare":
            caller.msg("Get what?")
            return CLAIM
        if action._usage:
            caller.msg("Usage: get <item> from <container>")
            return CLAIM
        if action.mode == "from":
            return SKIP
        objs = getattr(action, "_get_objs", None)
        if not objs:
            return CLAIM
        moved = []
        for obj in objs:
            if obj.move_to(caller, quiet=True, move_type="get"):
                moved.append(obj)
                obj.at_post_get(caller)
        if not moved:
            caller.msg("That can't be picked up.")
        else:
            obj_name = moved[0].get_numbered_name(len(moved), caller, return_string=True)
            caller.location.msg_contents(f"$You() $conj(pick) up {obj_name}.", from_obj=caller)
        return CLAIM

    # --- drop ----------------------------------------------------------------

    @rule(Drop, phase="check", priority=90)
    def check_drop(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        if action.mode == "bare" or not action.obj_spec:
            return action.block(0, "Drop what?")
        caller = self
        args = action.obj_spec
        from evennia.objects.search_result import Ambiguous, Found
        from evennia.utils import utils

        _r = caller.search_for(args, location=caller, stacked=action.count)
        if isinstance(_r, Found):
            pre = list(_r.stack) if _r.stack else [_r.obj]
        elif isinstance(_r, Ambiguous):
            pre = list(_r.candidates)
        else:
            pre = []
        if pre and not all(obj.location == caller for obj in pre):
            return action.block(0, f"You aren't carrying {args}.")
        objs = caller.search(
            args,
            location=caller,
            not_found=f"You aren't carrying {args}.",
            ambiguous=f"You carry more than one {args}:",
            stacked=action.count,
        )
        if not objs:
            return CLAIM
        objs = utils.make_iter(objs)
        from evennia.utils.utils import is_veto

        for obj in objs:
            if is_veto(obj.at_pre_drop(caller)):
                return CLAIM
        action._drop_objs = objs
        return PASS

    @rule(Drop, phase="carry_out", priority=50)
    def carry_out_drop(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        objs = getattr(action, "_drop_objs", None)
        if not objs:
            return CLAIM
        caller = self
        moved = []
        for obj in objs:
            if obj.move_to(caller.location, quiet=True, move_type="drop"):
                moved.append(obj)
                obj.at_post_drop(caller)
        if not moved:
            caller.msg("That can't be dropped.")
        else:
            obj_name = moved[0].get_numbered_name(len(moved), caller, return_string=True)
            caller.location.msg_contents(f"$You() $conj(drop) {obj_name}.", from_obj=caller)
        return CLAIM

    # --- give ----------------------------------------------------------------

    @rule(Give, phase="check", priority=90)
    def check_give(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        if action.mode == "usage":
            return action.block(0, "Usage: give <inventory object> = <target>")
        caller = self
        lhs = action.item_spec
        from evennia.objects.search_result import Ambiguous, Found
        from evennia.utils import utils

        _r = caller.search_for(lhs, location=caller, stacked=action.count)
        if isinstance(_r, Found):
            pre = list(_r.stack) if _r.stack else [_r.obj]
        elif isinstance(_r, Ambiguous):
            pre = list(_r.candidates)
        else:
            pre = []
        if pre and not all(obj.location == caller for obj in pre):
            return action.block(0, f"You aren't carrying {lhs}.")
        to_give = caller.search(
            lhs,
            location=caller,
            not_found=f"You aren't carrying {lhs}.",
            ambiguous=f"You carry more than one {lhs}:",
            stacked=action.count,
        )
        if not to_give:
            return CLAIM
        target = caller.search(action.target_spec)
        if not target:
            return CLAIM
        to_give = utils.make_iter(to_give)
        singular, plural = to_give[0].get_numbered_name(len(to_give), caller)
        if target == caller:
            return action.block(
                0, f"You keep {plural if len(to_give) > 1 else singular} to yourself."
            )
        from evennia.utils.utils import is_veto

        for obj in to_give:
            if is_veto(obj.at_pre_give(caller, target)):
                return CLAIM
        action._give_objs = to_give
        action._give_target = target
        return PASS

    @rule(Give, phase="carry_out", priority=50)
    def carry_out_give(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        to_give = getattr(action, "_give_objs", None)
        target = getattr(action, "_give_target", None)
        if not to_give or target is None:
            return CLAIM
        caller = self
        moved = []
        for obj in to_give:
            if obj.move_to(target, quiet=True, move_type="give"):
                moved.append(obj)
                obj.at_post_give(caller, target)
        if not moved:
            caller.msg(f"You could not give that to {target.get_display_name(caller)}.")
        else:
            obj_name = to_give[0].get_numbered_name(len(moved), caller, return_string=True)
            caller.msg(f"You give {obj_name} to {target.get_display_name(caller)}.")
            target.msg(f"{caller.get_display_name(target)} gives you {obj_name}.")
        return CLAIM

    # --- put (character preflight) -------------------------------------------

    @rule(Put, phase="check", priority=95)
    def check_put_character(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        if action._usage:
            return action.block(0, "Usage: put <item> in <container>")
        if action._unresolved:
            return CLAIM
        caller = self
        obj = action.target
        container = action.container
        if container == caller:
            return action.block(0, "You can't put something into yourself.")
        if obj == container:
            return action.block(0, "You can't put something into itself.")
        if obj.location != caller:
            return action.block(0, "You're not holding that.")
        return PASS

    # --- enter (character preflight) -----------------------------------------

    @rule(Enter, phase="check", priority=100)
    def check_enter_bare(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        if action.target is None and not action._unresolved:
            return action.block(0, "Enter what? Usage: enter <object>")
        if action._unresolved:
            return CLAIM
        return PASS


# --------------------------------------------------------------------------- #
# Container-side put rules
# --------------------------------------------------------------------------- #


class ContainerPutRules:
    """Baseline container rules for :class:`Put`, mixed into container typeclasses.

    The container is the :attr:`~evennia.actions.action.Action.__primary_handler__`
    and owns the canonical ``carry_out``. Every rule guards
    ``action.container is self``.
    """

    def _is_container(self, action) -> bool:
        return action.container is self

    @rule(Put, phase="check", priority=80)
    def check_put_container(self, action, actor):
        if not self._is_container(action):
            return SKIP
        caller = getattr(actor, "character", None)
        if caller is None:
            return SKIP
        if not self.access(caller, "get"):
            allow_put = getattr(self.db, "allow_put_while_get_false", False) or getattr(
                type(self), "fixture_allows_put_without_get", False
            )
            if not allow_put:
                return action.block(0, "You can't put anything in that.")
        obj = action.target
        from evennia.utils.utils import is_veto

        if hasattr(self, "at_pre_arrive") and is_veto(self.at_pre_arrive(obj, caller)):
            return CLAIM
        return PASS

    @rule(Put, phase="carry_out", priority=50)
    def carry_out_put(self, action, actor):
        if not self._is_container(action):
            return SKIP
        if action._unresolved:
            return CLAIM
        caller = getattr(actor, "character", None)
        if caller is None:
            return SKIP
        obj = action.target
        if not obj.move_to(self, quiet=True):
            caller.msg("You can't put that in there.")
            return CLAIM
        if hasattr(self, "at_post_arrive"):
            self.at_post_arrive(obj, caller)
        obj_name = obj.get_numbered_name(1, caller, return_string=True)
        cont_name = self.get_display_name(caller)
        caller.msg(f"You put {obj_name} in {cont_name}.")
        caller.location.msg_contents(
            f"$You() $conj(put) {obj_name} in {cont_name}.", from_obj=caller
        )
        return CLAIM


# --------------------------------------------------------------------------- #
# Enterable-side enter rules
# --------------------------------------------------------------------------- #


class EnterableObjectRules:
    """Baseline enter rules for :class:`Enter`, mixed into enterable typeclasses.

    The enterable target is the primary handler and owns ``carry_out``.
    """

    def _is_target(self, action) -> bool:
        return action.target is self

    @rule(Enter, phase="check", priority=90)
    def check_enterable(self, action, actor):
        if not self._is_target(action):
            return SKIP
        if action._unresolved:
            return CLAIM
        if not callable(getattr(self, "at_enter", None)):
            return action.block(0, "You can't enter that.")
        return PASS

    @rule(Enter, phase="carry_out", priority=50)
    def carry_out_enter(self, action, actor):
        if not self._is_target(action):
            return SKIP
        if action._unresolved:
            return CLAIM
        caller = getattr(actor, "character", None)
        if caller is None:
            return SKIP
        self.at_enter(caller)
        return CLAIM
