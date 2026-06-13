"""Tests for the engine-shipped admin actions (``evennia.actions.default.admin``).

The action-engine analogue of the ``CmdEmit``/``CmdWall``/``CmdForce``/
``CmdPerm``/``CmdAccess`` command tests: each verb dispatches through a real
:class:`~evennia.actions.engine.RuleEngine` over fake world objects, asserting
player-visible messages, side effects, and the ``requires=`` capability gates
(a non-staff actor must leave the dispatch fully gated, never half-run).
"""

import unittest
from unittest import mock

from evennia.actions.default import admin as admin_module
from evennia.actions.default.admin import (
    Access,
    CharacterAdminRules,
    Emit,
    Force,
    Perm,
    Wall,
)
from evennia.actions.tests.fakes import (
    FakeChar,
    FakeLocks,
    FakeObj,
    FakeSessionHandler,
    dispatch,
    make_actor,
)


class Staffer(CharacterAdminRules, FakeChar):
    """Provider fixture: a character carrying the admin rules."""


def _setup(perms=("Builder",)):
    char = Staffer(perms=perms)
    actor = make_actor(char)
    return char, actor


# --- @emit / @remit / @pemit -------------------------------------------------
class TestEmit(unittest.TestCase):
    def _emit(self, char, actor, raw, verb="@emit", switches=()):
        action = Emit.parse(raw, actor, switches=switches, verb=verb)
        return dispatch(action, actor, [char])

    def test_usage_without_args(self):
        char, actor = _setup()
        self._emit(char, actor, "")
        self.assertTrue(any("Usage" in m for m in char.messages))

    def test_emit_to_named_target(self):
        char, actor = _setup()
        bob = FakeObj(key="bob", location=FakeObj(key="room"))
        char.search_map["bob"] = bob
        self._emit(char, actor, "bob = hello there")
        self.assertIn("hello there", bob.messages)
        self.assertTrue(any("Emitted to bob" in m for m in char.messages))

    def test_emit_without_rhs_targets_location(self):
        char, actor = _setup()
        room = FakeObj(key="here")
        char.location = room
        char.search_map["here"] = room
        self._emit(char, actor, "something happens")
        self.assertIn("something happens", room.messages)

    def test_remit_forces_rooms_and_contents(self):
        char, actor = _setup()
        room = FakeObj(key="plaza")  # rooms have no location
        char.search_map["plaza"] = room
        self._emit(char, actor, "plaza = a wind blows", verb="@remit")
        self.assertIn("a wind blows", room.messages)
        self.assertIn("a wind blows", room.contents_messages)

    def test_remit_ignores_non_room(self):
        char, actor = _setup()
        bob = FakeObj(key="bob", location=FakeObj(key="room"))
        char.search_map["bob"] = bob
        self._emit(char, actor, "bob = hi", verb="@remit")
        self.assertNotIn("hi", bob.messages)
        self.assertTrue(any("not a room" in m for m in char.messages))

    def test_pemit_ignores_unpuppeted(self):
        char, actor = _setup()
        bob = FakeObj(key="bob", puppeted=False)
        char.search_map["bob"] = bob
        self._emit(char, actor, "bob = psst", verb="@pemit")
        self.assertNotIn("psst", bob.messages)
        self.assertTrue(any("no active account" in m for m in char.messages))

    def test_tell_access_required(self):
        char, actor = _setup()
        bob = FakeObj(key="bob", access={"tell": False})
        char.search_map["bob"] = bob
        self._emit(char, actor, "bob = hi")
        self.assertNotIn("hi", bob.messages)
        self.assertTrue(any("not allowed to emit" in m for m in char.messages))

    def test_gated_for_non_builder(self):
        char, actor = _setup(perms=("Player",))
        trace = self._emit(char, actor, "bob = hi")
        self.assertFalse(char.messages)
        self.assertEqual(trace.carry_out_fired, 0)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @wall --------------------------------------------------------------------
class TestWall(unittest.TestCase):
    def _wall(self, char, actor, raw):
        action = Wall.parse(raw, actor, verb="@wall")
        return dispatch(action, actor, [char])

    def test_usage_without_args(self):
        char, actor = _setup(perms=("Admin",))
        self._wall(char, actor, "")
        self.assertTrue(any("Usage" in m for m in char.messages))

    def test_announces_to_all_sessions(self):
        char, actor = _setup(perms=("Admin",))
        handler = FakeSessionHandler()
        with mock.patch.object(admin_module.evennia, "SESSION_HANDLER", handler):
            self._wall(char, actor, "server restart soon")
        self.assertEqual(handler.announced, ['Staff shouts "server restart soon"'])
        self.assertTrue(any("Announcing" in m for m in char.messages))

    def test_gated_for_builder(self):
        char, actor = _setup(perms=("Builder",))
        trace = self._wall(char, actor, "hi all")
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @force --------------------------------------------------------------------
class TestForce(unittest.TestCase):
    def _force(self, char, actor, raw):
        action = Force.parse(raw, actor, verb="@force")
        return dispatch(action, actor, [char])

    def test_usage_without_target_or_command(self):
        char, actor = _setup()
        self._force(char, actor, "bob")
        self.assertTrue(any("target and a command" in m for m in char.messages))

    def test_edit_access_required(self):
        char, actor = _setup()
        bob = FakeObj(key="bob", access={"edit": False})
        char.search_map["bob"] = bob
        self._force(char, actor, "bob = get stick")
        self.assertEqual(bob.executed, [])
        self.assertTrue(any("permission" in m for m in char.messages))

    def test_forces_command_execution(self):
        char, actor = _setup()
        bob = FakeObj(key="bob")
        char.search_map["bob"] = bob
        self._force(char, actor, "bob = get stick")
        self.assertEqual(bob.executed, ["get stick"])
        self.assertTrue(any("You have forced bob to: get stick" in m for m in char.messages))

    def test_gated_for_non_builder(self):
        char, actor = _setup(perms=("Player",))
        trace = self._force(char, actor, "bob = get stick")
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @perm ---------------------------------------------------------------------
class TestPerm(unittest.TestCase):
    def _perm(self, char, actor, raw, switches=()):
        action = Perm.parse(raw, actor, switches=switches, verb="@perm")
        return dispatch(action, actor, [char])

    def test_usage_without_args(self):
        char, actor = _setup(perms=("Developer",))
        self._perm(char, actor, "")
        self.assertTrue(any("Usage" in m for m in char.messages))

    def test_view_permissions(self):
        char, actor = _setup(perms=("Developer",))
        bob = FakeObj(key="bob", perms=("Helper",))
        char.search_map["bob"] = bob
        self._perm(char, actor, "bob")
        self.assertTrue(any("Permissions on" in m and "helper" in m for m in char.messages))

    def test_add_permission(self):
        char, actor = _setup(perms=("Developer",))
        bob = FakeObj(key="bob")
        char.search_map["bob"] = bob
        self._perm(char, actor, "bob = Helper")
        self.assertTrue(bob.permissions.get("helper"))
        self.assertTrue(any("given to bob" in m for m in char.messages))
        self.assertTrue(any("gives you" in m for m in bob.messages))

    def test_already_defined(self):
        char, actor = _setup(perms=("Developer",))
        bob = FakeObj(key="bob", perms=("Helper",))
        char.search_map["bob"] = bob
        self._perm(char, actor, "bob = Helper")
        self.assertTrue(any("already defined" in m for m in char.messages))

    def test_escalation_blocked(self):
        char, actor = _setup(perms=("Developer",))
        bob = FakeObj(key="bob")
        bob.locks = FakeLocks(allow=False)  # caller fails the perm() lock check
        char.search_map["bob"] = bob
        self._perm(char, actor, "bob = Developer")
        self.assertFalse(bob.permissions.get("developer"))
        self.assertTrue(any("cannot assign" in m for m in char.messages))

    def test_delete_permission(self):
        char, actor = _setup(perms=("Developer",))
        bob = FakeObj(key="bob", perms=("Helper",))
        char.search_map["bob"] = bob
        self._perm(char, actor, "bob = Helper", switches=("del",))
        self.assertFalse(bob.permissions.get("helper"))
        self.assertTrue(any("removed from bob" in m for m in char.messages))

    def test_account_mode_star_prefix(self):
        char, actor = _setup(perms=("Developer",))
        bobacct = FakeObj(key="bob")
        char.account_search_map["bob"] = bobacct
        self._perm(char, actor, "*bob = Helper")
        self.assertTrue(bobacct.permissions.get("helper"))
        self.assertTrue(any("the Account" in m for m in char.messages))

    def test_edit_access_required_for_change(self):
        char, actor = _setup(perms=("Developer",))
        bob = FakeObj(key="bob", access={"control": False})
        char.search_map["bob"] = bob
        self._perm(char, actor, "bob = Helper")
        self.assertFalse(bob.permissions.get("helper"))
        self.assertTrue(any("not allowed to edit" in m for m in char.messages))

    def test_gated_for_admin(self):
        # @perm is Developer-gated; even Admin is refused.
        char, actor = _setup(perms=("Admin",))
        trace = self._perm(char, actor, "bob = Helper")
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @access --------------------------------------------------------------------
class TestAccess(unittest.TestCase):
    def _access(self, char, actor):
        action = Access.parse("", actor, verb="@access")
        return dispatch(action, actor, [char])

    def test_shows_hierarchy_and_own_perms(self):
        char, actor = _setup(perms=("Builder",))
        self._access(char, actor)
        out = "\n".join(char.messages)
        self.assertIn("Permission Hierarchy", out)
        self.assertIn("builder", out)

    def test_ungated_for_player(self):
        char, actor = _setup(perms=("Player",))
        trace = self._access(char, actor)
        self.assertTrue(any("Permission Hierarchy" in m for m in char.messages))
        self.assertEqual(trace.carry_out_gated, 0)

    def test_superuser_account_branch(self):
        char, actor = _setup(perms=("Player",))
        char.account = FakeObj(key="boss")
        char.account.is_superuser = True
        self._access(char, actor)
        out = "\n".join(char.messages)
        self.assertIn("<Superuser>", out)


if __name__ == "__main__":
    unittest.main()
