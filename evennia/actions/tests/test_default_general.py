"""Tests for the engine-shipped general actions (``evennia.actions.default.general``).

The action-engine analogue of the ``CmdNick``/``CmdHome`` command tests: each
verb dispatches through a real :class:`~evennia.actions.engine.RuleEngine` over
fake world objects, asserting player-visible messages, nick-handler effects,
movement, and the ``requires=`` gates.
"""

import sys
import unittest
from unittest import mock

from twisted.internet.defer import Deferred

from evennia.actions.default import general as general_module
from evennia.actions.default.general import CharacterGeneralRules, Home, Nick, SetHelp
from evennia.actions.tests.fakes import FakeChar, FakeObj, dispatch, make_actor
from evennia.authorization.policy import Always, RequiresCapability
from evennia.typeclasses.attributes import NickTemplateInvalid

engine_mod = sys.modules["evennia.actions.engine"]


class FakeNick:
    """One stored nick: mirrors the Attribute surface the command reads."""

    def __init__(self, key, repl, category):
        self.key = key
        self.repl = repl
        self.category = category
        self.db_category = category

    @property
    def value(self):
        return (None, None, self.key, self.repl)


class FakeNicks:
    """Nick handler stub mirroring ``NickHandler``'s get/add/remove/clear."""

    def __init__(self, raise_template=False):
        self._nicks = []
        self.raise_template = raise_template
        self.cleared = False

    def get(self, key=None, category="inputline", return_obj=False):
        matches = [n for n in self._nicks if n.category == category]
        if key is not None:
            for nick in matches:
                if nick.key == key:
                    return nick
            return None
        return matches or None

    def add(self, key, repl, category="inputline"):
        if self.raise_template:
            raise NickTemplateInvalid()
        existing = self.get(key=key, category=category)
        if existing:
            existing.repl = repl
        else:
            self._nicks.append(FakeNick(key, repl, category))

    def remove(self, key, category="inputline"):
        self._nicks = [n for n in self._nicks if not (n.key == key and n.category == category)]

    def clear(self):
        self._nicks = []
        self.cleared = True


class Citizen(CharacterGeneralRules, FakeChar):
    """Provider fixture: a character carrying the general rules + nicks."""

    def __init__(self, capabilities=(), **kwargs):
        super().__init__(capabilities=capabilities, **kwargs)
        self.nicks = FakeNicks()


def _setup(capabilities=()):
    char = Citizen(capabilities=capabilities)
    actor = make_actor(char)
    return char, actor


# --- @nick ---------------------------------------------------------------------
class TestNick(unittest.TestCase):
    def _nick(self, char, actor, raw, verb="@nick", switches=()):
        action = Nick.parse(raw, actor, switches=switches, verb=verb)
        return dispatch(action, actor, [char])

    def test_bare_usage(self):
        char, actor = _setup()
        self._nick(char, actor, "")
        self.assertTrue(any("Usage" in m for m in char.messages))

    def test_add_nick(self):
        char, actor = _setup()
        self._nick(char, actor, "hi = say Hello")
        nick = char.nicks.get(key="hi", category="inputline")
        self.assertIsNotNone(nick)
        self.assertEqual(nick.repl, "say Hello")
        self.assertTrue(any("mapped to" in m for m in char.messages))

    def test_update_nick(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        self._nick(char, actor, "hi = say Goodbye")
        self.assertEqual(char.nicks.get(key="hi").repl, "say Goodbye")
        self.assertTrue(any("updated to map to" in m for m in char.messages))

    def test_identical_nick(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        self._nick(char, actor, "hi = say Hello")
        self.assertTrue(any("Identical" in m for m in char.messages))

    def test_nick_same_as_replacement_refused(self):
        char, actor = _setup()
        self._nick(char, actor, "hi = hi")
        self.assertTrue(any("No point" in m for m in char.messages))

    def test_list_switch_and_nicks_alias(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        self._nick(char, actor, "", switches=("list",))
        self.assertTrue(any("Defined Nicks" in m for m in char.messages))
        char.messages.clear()
        self._nick(char, actor, "", verb="@nicks")
        self.assertTrue(any("Defined Nicks" in m for m in char.messages))

    def test_list_empty(self):
        char, actor = _setup()
        self._nick(char, actor, "", switches=("list",))
        self.assertTrue(any("No nicks defined" in m for m in char.messages))

    def test_show_single_nick(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        self._nick(char, actor, "hi")
        self.assertTrue(any("'hi' -> 'say Hello'" in m for m in char.messages))

    def test_show_unknown_nick(self):
        char, actor = _setup()
        self._nick(char, actor, "zzz")
        self.assertTrue(any("No nicks found matching" in m for m in char.messages))

    def test_delete_by_name(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        self._nick(char, actor, "hi", switches=("delete",))
        self.assertIsNone(char.nicks.get(key="hi"))
        self.assertTrue(any("removed" in m for m in char.messages))

    def test_delete_by_index(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        self._nick(char, actor, "#1", switches=("delete",))
        self.assertIsNone(char.nicks.get(key="hi"))

    def test_delete_invalid_index(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        self._nick(char, actor, "#5", switches=("delete",))
        self.assertTrue(any("Not a valid nick index" in m for m in char.messages))

    def test_clearall_clears_account_too(self):
        char, actor = _setup()
        char.nicks.add("hi", "say Hello")
        account = FakeObj(key="acct")
        account.nicks = FakeNicks()
        char.account = account
        self._nick(char, actor, "", switches=("clearall",))
        self.assertTrue(char.nicks.cleared)
        self.assertTrue(account.nicks.cleared)
        self.assertTrue(any("Cleared all nicks" in m for m in char.messages))

    def test_escaped_equals_in_lhs(self):
        char, actor = _setup()
        self._nick(char, actor, r"tm\= $1 = @page tallman=$1")
        self.assertIsNotNone(char.nicks.get(key="tm= $1", category="inputline"))

    def test_template_mismatch_message(self):
        char, actor = _setup()
        char.nicks.raise_template = True
        self._nick(char, actor, "build $1 = @create")
        self.assertTrue(any("same $-markers" in m for m in char.messages))


# --- home ------------------------------------------------------------------------
class TestHome(unittest.TestCase):
    def _home(self, char, actor, raw=""):
        action = Home.parse(raw, actor, verb="home")
        return dispatch(action, actor, [char])

    def test_no_home(self):
        char, actor = _setup(capabilities=("engine.world.build",))
        char.home = None
        self._home(char, actor)
        self.assertTrue(any("no home" in m for m in char.messages))

    def test_already_home(self):
        char, actor = _setup(capabilities=("engine.world.build",))
        room = FakeObj(key="cottage")
        char.home = room
        char.location = room
        self._home(char, actor)
        self.assertTrue(any("already home" in m for m in char.messages))

    def test_teleports_home(self):
        char, actor = _setup(capabilities=("engine.world.build",))
        cottage = FakeObj(key="cottage")
        plaza = FakeObj(key="plaza")
        char.home = cottage
        char.location = plaza
        self._home(char, actor)
        self.assertEqual(char.location, cottage)
        self.assertEqual(char.moves[-1][1].get("move_type"), "teleport")
        self.assertTrue(any("no place like home" in m for m in char.messages))

    def test_args_show_usage(self):
        char, actor = _setup(capabilities=("engine.world.build",))
        char.home = FakeObj(key="cottage")
        self._home(char, actor, "north")
        self.assertEqual(char.moves, [])
        self.assertTrue(any("Usage: home" in m for m in char.messages))

    def test_gated_for_player(self):
        char, actor = _setup()
        char.home = FakeObj(key="cottage")
        trace = self._home(char, actor)
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @sethelp --------------------------------------------------------------------
class FakePolicies:
    """Policy handler stub for a help entry."""

    def __init__(self):
        self._policies = {"read": Always()}

    def get(self, operation):
        return self._policies.get(operation)

    def set(self, operation, policy):
        self._policies[operation] = policy


class FakeAliases:
    def __init__(self):
        self.added = []

    def add(self, aliases):
        self.added.append(aliases)


class FakeHelpEntry:
    """A DB help entry stub: just the attribute surface ``@sethelp`` touches."""

    def __init__(self, key="lore", entrytext="old text", help_category="general"):
        self.key = key
        self.entrytext = entrytext
        self.help_category = help_category
        self.policies = FakePolicies()
        self.aliases = FakeAliases()
        self.saved = False
        self.deleted = False

    def save(self):
        self.saved = True

    def delete(self):
        self.deleted = True


class StubHelper:
    """Stand-in for the throwaway ``CmdHelp`` search utility.

    ``collect_topics`` returns the controlled three-dict universe; ``do_search``
    pops a scripted result per call so a clash flow can match on the first query
    and miss the db-only re-search.
    """

    def __init__(self, search_results=(), topics=((), {}, {})):
        self._results = list(search_results)
        self._topics = topics

    def collect_topics(self, caller, mode="query"):
        cmd, db, file = self._topics
        return dict(cmd), dict(db), dict(file)

    def do_search(self, query, entries):
        if self._results:
            return self._results.pop(0), None
        return None, None


class Helpdesk(CharacterGeneralRules, FakeChar):
    """Provider fixture: a character carrying the general rules, Helper-ranked."""

    def __init__(self, capabilities=("engine.help.manage",), **kwargs):
        super().__init__(capabilities=capabilities, **kwargs)
        self.nicks = FakeNicks()


class TestSetHelp(unittest.TestCase):
    def _setup(self, capabilities=("engine.help.manage",)):
        char = Helpdesk(capabilities=capabilities)
        actor = make_actor(char)
        return char, actor

    def _sethelp(self, char, actor, raw, switches=(), helper=None):
        action = SetHelp.parse(raw, actor, switches=switches, verb="@sethelp")
        patch = mock.patch.object(
            general_module, "_sethelp_search_helper", return_value=helper or StubHelper()
        )
        with patch:
            return dispatch(action, actor, [char])

    def test_gated_for_player(self):
        char, actor = self._setup(capabilities=())
        action = SetHelp.parse("lore = text", actor, verb="@sethelp")
        trace = dispatch(action, actor, [char])
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)

    def test_no_args_usage(self):
        char, actor = self._setup()
        self._sethelp(char, actor, "")
        self.assertTrue(any("Usage: @sethelp" in m for m in char.messages))

    def test_add_new_entry(self):
        char, actor = self._setup()
        with mock.patch("evennia.utils.create.create_help_entry", return_value=object()) as mk:
            self._sethelp(char, actor, "lore = In the beginning")
        mk.assert_called_once()
        args, kwargs = mk.call_args
        self.assertEqual(args[0], "lore")
        self.assertEqual(args[1], "In the beginning")
        self.assertTrue(any("successfully created" in m for m in char.messages))

    def test_existing_entry_no_switch_warns(self):
        char, actor = self._setup()
        entry = FakeHelpEntry(key="lore")
        helper = StubHelper(search_results=[entry])
        self._sethelp(char, actor, "lore = new text", helper=helper)
        self.assertTrue(any("already exists" in m for m in char.messages))

    def test_replace_overwrites(self):
        char, actor = self._setup()
        entry = FakeHelpEntry(key="lore", entrytext="old")
        helper = StubHelper(search_results=[entry])
        self._sethelp(char, actor, "lore = brand new", switches=("replace",), helper=helper)
        self.assertEqual(entry.entrytext, "brand new")
        self.assertTrue(entry.saved)
        self.assertTrue(any("Overwrote" in m for m in char.messages))

    def test_append_adds_text(self):
        char, actor = self._setup()
        entry = FakeHelpEntry(key="lore", entrytext="line one")
        helper = StubHelper(search_results=[entry])
        self._sethelp(char, actor, "lore = line two", switches=("append",), helper=helper)
        self.assertEqual(entry.entrytext, "line one\nline two")
        self.assertTrue(any("Entry updated" in m for m in char.messages))

    def test_delete_removes_entry(self):
        char, actor = self._setup()
        entry = FakeHelpEntry(key="lore")
        helper = StubHelper(search_results=[entry])
        self._sethelp(char, actor, "lore", switches=("delete",), helper=helper)
        self.assertTrue(entry.deleted)
        self.assertTrue(any("Deleted help entry" in m for m in char.messages))

    def test_category_change(self):
        char, actor = self._setup()
        entry = FakeHelpEntry(key="lore", help_category="general")
        helper = StubHelper(search_results=[entry])
        self._sethelp(char, actor, "lore = classes", switches=("category",), helper=helper)
        self.assertEqual(entry.help_category, "classes")
        self.assertTrue(any("changed to 'classes'" in m for m in char.messages))

    def test_policy_change(self):
        char, actor = self._setup()
        entry = FakeHelpEntry(key="lore")
        helper = StubHelper(search_results=[entry])
        self._sethelp(
            char,
            actor,
            "lore = engine.world.build",
            switches=("policy",),
            helper=helper,
        )
        self.assertEqual(entry.policies.get("read"), RequiresCapability("engine.world.build"))
        self.assertTrue(any("engine.world.build" in m for m in char.messages))

    def test_clash_warning_abort(self):
        from evennia.help.formatters import HelpCategory

        char, actor = self._setup()
        helper = StubHelper(search_results=[HelpCategory("combat")])
        answers = iter(["n"])

        def fake_input(actor_, prompt):
            d = Deferred()
            char.msg(prompt)
            d.callback(next(answers))
            return d

        with mock.patch.object(engine_mod, "_get_input_future", side_effect=fake_input):
            with mock.patch("evennia.utils.create.create_help_entry") as mk:
                self._sethelp(char, actor, "combat = how to fight", helper=helper)
        self.assertTrue(any("Warning" in m for m in char.messages))
        self.assertTrue(any("Aborted" in m for m in char.messages))
        mk.assert_not_called()

    def test_clash_warning_continue_creates_entry(self):
        from evennia.help.formatters import HelpCategory

        char, actor = self._setup()
        # match the category on the first search, miss the db-only re-search.
        helper = StubHelper(search_results=[HelpCategory("combat"), None])
        answers = iter(["y"])

        def fake_input(actor_, prompt):
            d = Deferred()
            char.msg(prompt)
            d.callback(next(answers))
            return d

        with mock.patch.object(engine_mod, "_get_input_future", side_effect=fake_input):
            with mock.patch("evennia.utils.create.create_help_entry", return_value=object()) as mk:
                self._sethelp(char, actor, "combat = how to fight", helper=helper)
        self.assertTrue(any("Warning" in m for m in char.messages))
        mk.assert_called_once()

    def test_verb_clash_warning_text(self):
        from evennia.help.catalog import ActionHelpTopic

        char, actor = self._setup()
        topic = ActionHelpTopic("look", object, "general", [], "look around", True)
        helper = StubHelper(search_results=[topic, None])
        answers = iter(["n"])

        def fake_input(actor_, prompt):
            d = Deferred()
            char.msg(prompt)
            d.callback(next(answers))
            return d

        with mock.patch.object(engine_mod, "_get_input_future", side_effect=fake_input):
            self._sethelp(char, actor, "look = my topic", helper=helper)
        self.assertTrue(any("key/alias of verb 'look'" in m for m in char.messages))

    def test_edit_opens_editor_and_captures_through_engine(self):
        from evennia.actions.action import Action
        from evennia.actions.context import ActionContext
        from evennia.actions.tests.fakes import ENGINE, _sync
        from evennia.utils.eveditor import EvEditorState

        char, actor = self._setup()
        entry = FakeHelpEntry(key="lore", entrytext="")
        with mock.patch("evennia.utils.create.create_help_entry", return_value=entry):
            self._sethelp(char, actor, "lore", switches=("edit",))
        self.assertTrue(actor.has_state(EvEditorState))

        state = actor.state_objects[-1]
        line = Action()
        line._raw_string = "a help line"
        ctx = ActionContext(providers=[state], actor=actor, raw_string="a help line")
        _sync(ENGINE.dispatch(line, actor, ctx, record_phases=False))
        self.assertIn("a help line", char.ndb._eveditor.get_buffer())


if __name__ == "__main__":
    unittest.main()
