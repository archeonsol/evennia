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
* :class:`SetHelp` — edit the in-DB help database (``@sethelp``), Helper-gated.
  Its clash-warning branch is a ``yield``-confirm the engine's generator driver
  fills; ``/edit`` opens ``EvEditor`` (input captured engine-side as a
  :class:`~evennia.actions.state.StateProvider`). The help redesign authored
  command help as explicit files, but DB help entries are unchanged, so
  ``@sethelp`` still edits them. Character-side only, matching the stock
  ``CmdSetHelp`` living only in the Character cmdset.

:class:`NickRules` is a standalone mixin (guarding ``self is
actor.effective``) so the account shell can reuse it, mirroring the stock
``CmdNick`` appearing in both the Character and Account cmdsets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from evennia.authorization.policy import Always, Never, PredicateRequirement, RequiresCapability
from evennia.objects.character import DefaultCharacter

from ..action import action
from ..muxargs import ArgAction
from ..predicate import HasCapability
from ..result import CLAIM, SKIP
from ..rule import rule

__all__ = [
    "Nick",
    "Home",
    "Help",
    "Look",
    "Quit",
    "SetHelp",
    "NickRules",
    "CharacterGeneralRules",
]


def _help_policy(declaration):
    """Compile one public-or-capability help declaration."""

    value = str(declaration or "public").strip().lower()
    return Always() if value == "public" else RequiresCapability(value)


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
        return cls(
            args=args,
            switches=tuple(switches or ()),
            verb=(verb or ""),
            lhs=lhs,
            rhs=rhs,
        )


@action("home")
@dataclass
class Home(ArgAction):
    """Teleport to your home location (``home``)."""

    __primary_handler__ = DefaultCharacter


@action("@sethelp")
@dataclass
class SetHelp(ArgAction):
    """Edit the in-DB help database.

    ``@sethelp[/edit|/replace|/append|/extend|/category|/locks|/delete]
    <topic>[;alias;alias][,category[,locks]] [= <text or new value>]``. The
    standard mux lhs/rhs and comma splits apply; aliases are parsed from the
    topic's ``;``-list in the rule, as the stock command does.
    """

    __primary_handler__ = DefaultCharacter


@action("help", "h", "?")
@dataclass
class Help(ArgAction):
    """Show help (``help [topic]``).

    The engine ships only the unlogged-in baseline rule (see
    :class:`~evennia.actions.default.unloggedin.SessionLoginRules`); a game
    binds its own help system as rules on this action type.
    """


@action("look", "l")
@dataclass
class Look(ArgAction):
    """Look at your surroundings (``look [target]``).

    The engine ships only the unlogged-in baseline rule (re-render the
    connection screen; see
    :class:`~evennia.actions.default.unloggedin.SessionLoginRules`); a game
    binds in-character looking as rules on this action type.
    """


@action("quit")
@dataclass
class Quit(ArgAction):
    """Disconnect from the game (``quit``).

    The engine ships only the unlogged-in baseline rule (drop the connection;
    see :class:`~evennia.actions.default.unloggedin.SessionLoginRules`); a game
    binds its logged-in quit (e.g. unpuppet/confirm, or an ``@quit`` syntax) as
    rules on this action type.
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
                        str(inum + 1),
                        nickobj.db_category,
                        _cy(nickvalue),
                        _cy(replacement),
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


def _sethelp_search_helper(caller, session):
    """A bare ``HelpFormatter`` used purely as a stateless topic-lookup utility.

    ``@sethelp``'s clash warning must search the *same* topic universe the
    ``help`` command shows (cmd/db/file), so a builder is warned when a new DB
    entry would be shadowed by a verb, category, or file-help topic. Rather than
    reimplement that lookup (and risk drift from what players actually see), we
    reuse ``HelpFormatter.collect_topics``/``do_search`` through a throwaway
    instance. It is never merged into a cmdset or run through the cmdhandler -
    the verb itself is the native :class:`SetHelp` action; this is the same
    reuse the EvEditor does with its match-only ``CmdLineInput()``.
    """
    from evennia.help.formatters import HelpFormatter

    helper = HelpFormatter()
    helper.caller = caller
    helper.session = session
    helper.account = getattr(caller, "account", None)
    return helper


class CharacterGeneralRules(NickRules):
    """Baseline character-side general rules: nicks, ``home``, ``@sethelp``."""

    @rule(Home, phase="carry_out", requires=HasCapability("engine.world.build"))
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

    @rule(SetHelp, phase="carry_out", requires=HasCapability("engine.help.manage"))
    def carry_out_sethelp(self, action, actor):
        if self is not getattr(actor, "character", None):
            return SKIP
        # Returns a generator the engine drives; it yields only to confirm a
        # clashing help-entry name, every other path runs straight through.
        return self._sethelp_flow(self, action, actor)

    @staticmethod
    def _sethelp_flow(caller, action, actor):
        """Apply a ``@sethelp`` request (ports ``CmdSetHelp.func``).

        A generator: the only suspension point is the clash-warning confirm,
        whose ``yield`` the engine's generator driver fills with the player's
        reply. ``return CLAIM`` ends the flow as for any carry_out rule.
        """
        from evennia.help.catalog import is_action_help_topic
        from evennia.help.formatters import HelpCategory, _loadhelp, _quithelp, _savehelp
        from evennia.utils import create
        from evennia.utils.eveditor import EvEditor
        from evennia.utils.utils import inherits_from

        switches = action.switches
        lhslist = list(action.lhslist)
        rhslist = list(action.rhslist)
        session = getattr(actor, "session", None)

        if not action.args:
            caller.msg(
                "Usage: @sethelp[/switches] <topic>[;alias;alias][,category[,capability]]"
                " [= <text or new category>]"
            )
            return CLAIM

        nlist = len(lhslist)
        topicstr = lhslist[0] if nlist > 0 else ""
        if not topicstr:
            caller.msg("You have to define a topic!")
            return CLAIM
        topicstrlist = topicstr.split(";")
        topicstr, aliases = (
            topicstrlist[0],
            topicstrlist[1:] if len(topicstr) > 1 else [],
        )
        aliastxt = ("(aliases: %s)" % ", ".join(aliases)) if aliases else ""
        old_entry = None

        helper = _sethelp_search_helper(caller, session)
        cmd_help_topics, db_help_topics, file_help_topics = helper.collect_topics(
            caller, mode="query"
        )
        # db-help takes priority over file-help; verbs over either.
        file_db_help_topics = {**file_help_topics, **db_help_topics}
        all_topics = {**file_db_help_topics, **cmd_help_topics}
        all_categories = list(
            set(HelpCategory(topic.help_category) for topic in all_topics.values())
        )
        entries = list(all_topics.values()) + all_categories

        category = lhslist[1] if nlist > 1 else settings.DEFAULT_HELP_CATEGORY
        policy_declaration = ",".join(lhslist[2:]) if nlist > 2 else "public"

        for querystr in topicstrlist:
            match, _ = helper.do_search(querystr, entries)
            if not match:
                continue
            warning = None
            if isinstance(match, HelpCategory):
                warning = (
                    f"'{querystr}' matches (or partially matches) the name of "
                    f"help-category '{match.key}'. If you continue, your help entry will "
                    "take precedence and the category (or part of its name) *may* not "
                    "be usable for grouping help entries anymore."
                )
            elif is_action_help_topic(match):
                warning = (
                    f"'{querystr}' matches (or partially matches) the key/alias of "
                    f"verb '{match.key}'. Verb-help takes precedence over other "
                    "help entries so your help *may* be impossible to reach for those "
                    "with access to that verb."
                )
            elif inherits_from(match, "evennia.help.filehelp.FileHelpEntry"):
                warning = (
                    f"'{querystr}' matches (or partially matches) the name/alias of the "
                    f"file-based help topic '{match.key}'. File-help entries cannot be "
                    "modified from in-game (they are files on-disk). If you continue, "
                    "your help entry may shadow the file-based one's name partly or "
                    "completely."
                )
            if warning:
                caller.msg(f"|rWarning:\n|r{warning}|n")
                repl = yield ("|wDo you still want to continue? Y/[N]?|n")
                if (repl or "").lower() in ("y", "yes"):
                    db_topics = {**db_help_topics}
                    db_categories = list(
                        set(HelpCategory(topic.help_category) for topic in db_topics.values())
                    )
                    db_entries = list(db_topics.values()) + db_categories
                    match, _ = helper.do_search(querystr, db_entries)
                    if match:
                        old_entry = match
                else:
                    caller.msg("Aborted.")
                    return CLAIM
            else:
                old_entry = match
                category = lhslist[1] if nlist > 1 else old_entry.help_category
                policy_declaration = ",".join(lhslist[2:]) if nlist > 2 else "public"
                break

        category = category.lower()

        if "edit" in switches:
            if old_entry:
                topicstr = old_entry.key
                if action.rhs:
                    old_entry.entrytext += "\n%s" % action.rhs
                helpentry = old_entry
            else:
                helpentry = create.create_help_entry(
                    topicstr,
                    action.rhs if action.rhs is not None else "",
                    category=category,
                    policies={"read": _help_policy(policy_declaration)},
                    aliases=aliases,
                )
            caller.db._editing_help = helpentry
            EvEditor(
                caller,
                loadfunc=_loadhelp,
                savefunc=_savehelp,
                quitfunc=_quithelp,
                key="topic {}".format(topicstr),
                persistent=True,
            )
            return CLAIM

        if "append" in switches or "merge" in switches or "extend" in switches:
            if not old_entry:
                caller.msg(f"Could not find topic '{topicstr}'. You must give an exact name.")
                return CLAIM
            if not action.rhs:
                caller.msg("You must supply text to append/merge.")
                return CLAIM
            if "merge" in switches:
                old_entry.entrytext += " " + action.rhs
            else:
                old_entry.entrytext += "\n%s" % action.rhs
            old_entry.aliases.add(aliases)
            caller.msg(f"Entry updated:\n{old_entry.entrytext}{aliastxt}")
            return CLAIM

        if "category" in switches:
            if not old_entry:
                caller.msg(f"Could not find topic '{topicstr}'{aliastxt}.")
                return CLAIM
            if not action.rhs:
                caller.msg("You must supply a category.")
                return CLAIM
            category = action.rhs.lower()
            old_entry.help_category = category
            caller.msg(f"Category for entry '{topicstr}'{aliastxt} changed to '{category}'.")
            return CLAIM

        if "policy" in switches:
            if not old_entry:
                caller.msg(f"Could not find topic '{topicstr}'{aliastxt}.")
                return CLAIM
            show_policy = not rhslist
            clear_policy = rhslist and not rhslist[0]
            if show_policy:
                policy = old_entry.policies.get("read")
                caller.msg(
                    f"Current read policy for '{topicstr}'{aliastxt}: "
                    f"{policy.to_data() if policy else '<class default>'}"
                )
                return CLAIM
            if clear_policy:
                old_entry.policies.set("read", Always())
                caller.msg(f"Read policy for '{topicstr}'{aliastxt} reset to public.")
                return CLAIM
            try:
                policy = _help_policy(",".join(rhslist))
            except (TypeError, ValueError) as err:
                caller.msg(f"Policy not changed: {err}")
                return CLAIM
            old_entry.policies.set("read", policy)
            caller.msg(f"Read policy for '{topicstr}'{aliastxt} changed to: {policy.to_data()}")
            return CLAIM

        if "delete" in switches or "del" in switches:
            if not old_entry:
                caller.msg(f"Could not find topic '{topicstr}'{aliastxt}.")
                return CLAIM
            old_entry.delete()
            caller.msg(f"Deleted help entry '{topicstr}'{aliastxt}.")
            return CLAIM

        # add a new help entry (or /replace an existing one)
        if not action.rhs:
            caller.msg("You must supply a help text to add.")
            return CLAIM
        if old_entry:
            if "replace" in switches:
                old_entry.key = topicstr
                old_entry.entrytext = action.rhs
                old_entry.help_category = category
                old_entry.policies.set("read", _help_policy(policy_declaration))
                old_entry.aliases.add(aliases)
                old_entry.save()
                caller.msg(f"Overwrote the old topic '{topicstr}'{aliastxt}.")
            else:
                caller.msg(
                    f"Topic '{topicstr}'{aliastxt} already exists. Use /edit to open in editor, "
                    "or /replace, /append and /merge to modify it directly."
                )
        else:
            new_entry = create.create_help_entry(
                topicstr,
                action.rhs,
                category=category,
                policies={"read": _help_policy(policy_declaration)},
                aliases=aliases,
            )
            if new_entry:
                caller.msg(f"Topic '{topicstr}'{aliastxt} was successfully created.")
            else:
                caller.msg(f"Error when creating topic '{topicstr}'{aliastxt}! Contact an admin.")
        return CLAIM
