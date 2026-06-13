"""
Default general actions: engine-shipped player/builder utility verbs.

The action-engine analogue of ``evennia/commands/default/general.py``'s
``CmdNick`` and ``CmdHome``:

* :class:`Nick` — personal alias/replacement management (``@nick`` /
  ``@nicks`` / ``@nickname``), with the stock unescaped-``=`` split,
  list/delete/clearall switches, and ``$``-template validation. Only the
  stock command's *live* code path is ported (its source repeats the
  "show what a nick is set to" block three times; the later copies are
  unreachable and reference an undefined name).
* :class:`Home` — teleport to ``caller.home`` (Builder-gated, as stock).

``CmdSetHelp`` is **not** ported here: its clash-handling is a yield-based
confirmation flow and ``/edit`` opens ``EvEditor``, so it lands with the
interactive-verb work.

:class:`NickRules` is a standalone mixin (guarding ``self is
actor.effective``) so the account shell can reuse it, mirroring the stock
``CmdNick`` appearing in both the Character and Account cmdsets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from evennia.objects.character import DefaultCharacter

from ..action import action
from ..muxargs import ArgAction
from ..predicate import Builder as BuilderCap
from ..result import CLAIM, SKIP
from ..rule import rule

__all__ = [
    "Nick",
    "Home",
    "Help",
    "NickRules",
    "CharacterGeneralRules",
]


def _cy(string):
    """Colorize ``$``-markers and glob tokens in nick output."""
    return re.sub(r"(\$[0-9]+|\*|\?|\[.+?\])", r"|Y\1|n", string)


@action("@nick", "@nicks", "@nickname")
@dataclass
class Nick(ArgAction):
    """Define personal alias/replacement strings.

    ``@nick[/inputline|/object|/account|/list|/delete|/clearall]
    <string> [= [replacement]]``. The lhs/rhs split honors ``\\=`` escapes,
    exactly as the stock command's custom ``parse``.
    """

    __primary_handler__ = DefaultCharacter

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        args = (raw_args or "").strip()
        parts = re.split(r"(?<!\\)=", args, 1)
        rhs = None
        if len(parts) < 2:
            lhs = parts[0].strip()
        else:
            lhs, rhs = [part.strip() for part in parts]
        lhs = lhs.replace("\\=", "=")
        return cls(args=args, switches=tuple(switches or ()), verb=(verb or ""), lhs=lhs, rhs=rhs)


@action("home")
@dataclass
class Home(ArgAction):
    """Teleport to your home location (``home``)."""

    __primary_handler__ = DefaultCharacter


@action("help", "h", "?")
@dataclass
class Help(ArgAction):
    """Show help (``help [topic]``).

    The engine ships only the unlogged-in baseline rule (see
    :class:`~evennia.actions.default.unloggedin.SessionLoginRules`); a game
    binds its own help system as rules on this action type.
    """


class NickRules:
    """Baseline ``@nick`` rules; reusable by both character and account shells.

    The guard is ``self is actor.effective`` so the puppeted character handles
    the verb in-character and the account handles it OOC — matching the stock
    command's presence in both default cmdsets.
    """

    @rule(Nick, phase="carry_out")
    def carry_out_nick(self, action, actor):
        if self is not getattr(actor, "effective", None):
            return SKIP
        from evennia.typeclasses.attributes import NickTemplateInvalid
        from evennia.utils import evtable, utils

        caller = self
        switches = action.switches
        nicktypes = [sw for sw in switches if sw in ("object", "account", "inputline")]
        specified_nicktype = bool(nicktypes)
        nicktypes = nicktypes if specified_nicktype else ["inputline"]

        nicklist = (
            utils.make_iter(caller.nicks.get(category="inputline", return_obj=True) or [])
            + utils.make_iter(caller.nicks.get(category="object", return_obj=True) or [])
            + utils.make_iter(caller.nicks.get(category="account", return_obj=True) or [])
        )

        if "list" in switches or action.verb == "@nicks":
            if not nicklist:
                string = "|wNo nicks defined.|n"
            else:
                table = evtable.EvTable("#", "Type", "Nick match", "Replacement")
                for inum, nickobj in enumerate(nicklist):
                    _, _, nickvalue, replacement = nickobj.value
                    table.add_row(
                        str(inum + 1), nickobj.db_category, _cy(nickvalue), _cy(replacement)
                    )
                string = "|wDefined Nicks:|n\n%s" % table
            caller.msg(string)
            return CLAIM

        if "clearall" in switches:
            caller.nicks.clear()
            account = getattr(caller, "account", None)
            if account:
                account.nicks.clear()
            caller.msg("Cleared all nicks.")
            return CLAIM

        if "delete" in switches or "del" in switches:
            if not action.args or not action.lhs:
                caller.msg("usage nick/delete <nick> or <#num> ('nicks' for list)")
                return CLAIM
            arg = action.args.lstrip("#")
            oldnicks = []
            if arg.isdigit():
                delindex = int(arg)
                if 0 < delindex <= len(nicklist):
                    oldnicks.append(nicklist[delindex - 1])
                else:
                    caller.msg("Not a valid nick index. See 'nicks' for a list.")
                    return CLAIM
            else:
                if not specified_nicktype:
                    nicktypes = ("object", "account", "inputline")
                for nicktype in nicktypes:
                    oldnicks.append(caller.nicks.get(arg, category=nicktype, return_obj=True))

            oldnicks = [oldnick for oldnick in oldnicks if oldnick]
            if oldnicks:
                for oldnick in oldnicks:
                    nicktype = oldnick.category
                    nicktypestr = "%s-nick" % nicktype.capitalize()
                    _, _, old_nickstring, old_replstring = oldnick.value
                    caller.nicks.remove(old_nickstring, category=nicktype)
                    caller.msg(
                        f"{nicktypestr} removed: '|w{old_nickstring}|n' -> |w{old_replstring}|n."
                    )
            else:
                caller.msg("No matching nicks to remove.")
            return CLAIM

        if not action.rhs and action.lhs:
            # check what a nick is set to
            strings = []
            if not specified_nicktype:
                nicktypes = ("object", "account", "inputline")
            for nicktype in nicktypes:
                nicks = [
                    nick
                    for nick in utils.make_iter(
                        caller.nicks.get(category=nicktype, return_obj=True)
                    )
                    if nick
                ]
                for nick in nicks:
                    _, _, nick, repl = nick.value
                    if nick.startswith(action.lhs):
                        strings.append(f"{nicktype.capitalize()}-nick: '{nick}' -> '{repl}'")
            if strings:
                caller.msg("\n".join(strings))
            else:
                caller.msg(f"No nicks found matching '{action.lhs}'")
            return CLAIM

        if not action.args or not action.lhs:
            caller.msg("Usage: @nick[/switches] nickname = [realname]")
            return CLAIM

        # setting new nicks
        nickstring = action.lhs
        replstring = action.rhs

        if replstring == nickstring:
            caller.msg("No point in setting nick same as the string to replace...")
            return CLAIM

        errstring = ""
        string = ""
        for nicktype in nicktypes:
            nicktypestr = f"{nicktype.capitalize()}-nick"
            old_nickstring = None
            old_replstring = None

            oldnick = caller.nicks.get(key=nickstring, category=nicktype, return_obj=True)
            if oldnick:
                _, _, old_nickstring, old_replstring = oldnick.value
            if replstring:
                errstring = ""
                if oldnick:
                    if replstring == old_replstring:
                        string += f"\nIdentical {nicktypestr.lower()} already set."
                    else:
                        string += (
                            f"\n{nicktypestr} '|w{old_nickstring}|n' updated to map to"
                            f" '|w{replstring}|n'."
                        )
                else:
                    string += f"\n{nicktypestr} '|w{nickstring}|n' mapped to '|w{replstring}|n'."
                try:
                    caller.nicks.add(nickstring, replstring, category=nicktype)
                except NickTemplateInvalid:
                    caller.msg(
                        "You must use the same $-markers both in the nick and in the replacement."
                    )
                    return CLAIM
            elif old_nickstring and old_replstring:
                # just looking at the nick
                string += f"\n{nicktypestr} '|w{old_nickstring}|n' maps to '|w{old_replstring}|n'."
                errstring = ""
        string = errstring if errstring else string
        caller.msg(_cy(string))
        return CLAIM


class CharacterGeneralRules(NickRules):
    """Baseline character-side general rules: nicks plus ``home``."""

    @rule(Home, phase="carry_out", requires=BuilderCap)
    def carry_out_home(self, action, actor):
        if self is not getattr(actor, "character", None):
            return SKIP
        caller = self
        if action.args:
            caller.msg("Usage: home")
            return CLAIM
        home = caller.home
        if not home:
            caller.msg("You have no home!")
        elif home == caller.location:
            caller.msg("You are already home!")
        else:
            caller.msg("There's no place like home ...")
            caller.move_to(home, move_type="teleport")
        return CLAIM
