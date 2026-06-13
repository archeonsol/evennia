"""Tests for the engine-shipped general actions (``evennia.actions.default.general``).

The action-engine analogue of the ``CmdNick``/``CmdHome`` command tests: each
verb dispatches through a real :class:`~evennia.actions.engine.RuleEngine` over
fake world objects, asserting player-visible messages, nick-handler effects,
movement, and the ``requires=`` gates.
"""

import unittest

from evennia.actions.default.general import CharacterGeneralRules, Home, Nick
from evennia.actions.tests.fakes import FakeChar, FakeObj, dispatch, make_actor
from evennia.typeclasses.attributes import NickTemplateInvalid


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

    def __init__(self, perms=("Player",), **kwargs):
        super().__init__(perms=perms, **kwargs)
        self.nicks = FakeNicks()


def _setup(perms=("Player",)):
    char = Citizen(perms=perms)
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
        char, actor = _setup(perms=("Builder",))
        char.home = None
        self._home(char, actor)
        self.assertTrue(any("no home" in m for m in char.messages))

    def test_already_home(self):
        char, actor = _setup(perms=("Builder",))
        room = FakeObj(key="cottage")
        char.home = room
        char.location = room
        self._home(char, actor)
        self.assertTrue(any("already home" in m for m in char.messages))

    def test_teleports_home(self):
        char, actor = _setup(perms=("Builder",))
        cottage = FakeObj(key="cottage")
        plaza = FakeObj(key="plaza")
        char.home = cottage
        char.location = plaza
        self._home(char, actor)
        self.assertEqual(char.location, cottage)
        self.assertEqual(char.moves[-1][1].get("move_type"), "teleport")
        self.assertTrue(any("no place like home" in m for m in char.messages))

    def test_args_show_usage(self):
        char, actor = _setup(perms=("Builder",))
        char.home = FakeObj(key="cottage")
        self._home(char, actor, "north")
        self.assertEqual(char.moves, [])
        self.assertTrue(any("Usage: home" in m for m in char.messages))

    def test_gated_for_player(self):
        char, actor = _setup(perms=("Player",))
        char.home = FakeObj(key="cottage")
        trace = self._home(char, actor)
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


if __name__ == "__main__":
    unittest.main()
