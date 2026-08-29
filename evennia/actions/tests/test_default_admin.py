"""Tests for the engine-shipped admin actions (``evennia.actions.default.admin``).

The action-engine analogue of the ``CmdEmit``/``CmdWall``/``CmdForce``/
capability administration command tests: each verb dispatches through a real
:class:`~evennia.actions.engine.RuleEngine` over fake world objects, asserting
player-visible messages, side effects, and the ``requires=`` capability gates
(a non-staff actor must leave the dispatch fully gated, never half-run).
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from evennia.actions.default import admin as admin_module
from evennia.actions.default.admin import (
    Access,
    CharacterAdminRules,
    Emit,
    Force,
    Grant,
    Policy,
    Scope,
    Wall,
)
from evennia.actions.tests.fakes import (
    FakeChar,
    FakeObj,
    FakeSessionHandler,
    dispatch,
    make_actor,
)


class Staffer(CharacterAdminRules, FakeChar):
    """Provider fixture: a character carrying the admin rules."""


def _setup(capabilities=("engine.world.build",)):
    char = Staffer(capabilities=capabilities)
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

    def test_emit_to_a_room_reaches_its_contents(self):
        """A room has no sessions, so a bare @emit must fan out or reach nobody."""
        char, actor = _setup()
        room = FakeObj(key="here")  # rooms have no location
        char.location = room
        char.search_map["here"] = room
        self._emit(char, actor, "something happens")
        self.assertIn("something happens", room.contents_messages)
        self.assertTrue(any("and contents" in m for m in char.messages))

    def test_emit_to_a_non_room_does_not_fan_out(self):
        char, actor = _setup()
        bob = FakeObj(key="bob", location=FakeObj(key="room"))
        char.search_map["bob"] = bob
        self._emit(char, actor, "bob = hello there")
        self.assertIn("hello there", bob.messages)
        self.assertEqual([], bob.contents_messages)

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
        char, actor = _setup(capabilities=())
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
        char, actor = _setup(capabilities=("engine.moderation.manage",))
        self._wall(char, actor, "")
        self.assertTrue(any("Usage" in m for m in char.messages))

    def test_announces_to_all_sessions(self):
        char, actor = _setup(capabilities=("engine.moderation.manage",))
        handler = FakeSessionHandler()
        with mock.patch.object(admin_module.evennia, "SESSION_HANDLER", handler):
            self._wall(char, actor, "server restart soon")
        self.assertEqual(handler.announced, ['Staff shouts "server restart soon"'])
        self.assertTrue(any("Announcing" in m for m in char.messages))

    def test_gated_for_builder(self):
        char, actor = _setup(capabilities=("engine.world.build",))
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
        char, actor = _setup(capabilities=())
        trace = self._force(char, actor, "bob = get stick")
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @grant --------------------------------------------------------------------
class TestGrant(unittest.TestCase):
    def _grant(self, char, actor, raw, switches=()):
        action = Grant.parse(raw, actor, switches=switches, verb="@grant")
        return dispatch(action, actor, [char])

    def test_usage_without_args(self):
        char, actor = _setup(capabilities=("engine.runtime.manage",))
        self._grant(char, actor, "")
        self.assertTrue(any("Usage" in message for message in char.messages))

    def test_grants_registered_capability(self):
        char, actor = _setup(capabilities=("engine.runtime.manage",))
        char.pk = 1
        target = FakeObj(key="bob")
        target.pk = 2
        char.search_map["bob"] = target
        with mock.patch("evennia.authorization.storage.grant_capability") as grant:
            self._grant(char, actor, "bob = engine.world.build")
        grant.assert_called_once_with(
            "object:2",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            provenance="action_command",
            actor_ref="object:1",
            reason="action @grant",
        )
        self.assertTrue(any("Granted 1" in message for message in char.messages))

    def test_revoke_switch_revokes_a_matching_grant(self):
        char, actor = _setup(capabilities=("engine.runtime.manage",))
        char.pk = 1
        target = FakeObj(key="bob")
        target.pk = 2
        char.search_map["bob"] = target
        grant = SimpleNamespace(grant_id="grant-1")
        with (
            mock.patch("evennia.server.models.AuthorizationGrant") as grant_model,
            mock.patch("evennia.authorization.storage.revoke_grant", return_value=True) as revoke,
        ):
            grant_model.objects.filter.return_value.first.return_value = grant
            self._grant(char, actor, "bob = grant-1", switches=("revoke",))
        revoke.assert_called_once_with(
            "grant-1", actor_ref="object:1", reason="action @grant/revoke"
        )
        self.assertTrue(any("Grant revoked." in message for message in char.messages))

    def test_del_switch_remains_a_revoke_alias(self):
        char, actor = _setup(capabilities=("engine.runtime.manage",))
        char.pk = 1
        target = FakeObj(key="bob")
        target.pk = 2
        char.search_map["bob"] = target
        grant = SimpleNamespace(grant_id="grant-1")
        with (
            mock.patch("evennia.server.models.AuthorizationGrant") as grant_model,
            mock.patch("evennia.authorization.storage.revoke_grant", return_value=True) as revoke,
        ):
            grant_model.objects.filter.return_value.first.return_value = grant
            self._grant(char, actor, "bob = grant-1", switches=("del",))
        revoke.assert_called_once()

    def test_gated_without_runtime_manage(self):
        char, actor = _setup()
        trace = self._grant(char, actor, "bob = engine.world.build")
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


class FakePolicies:
    """Sparse policy handler used by admin-action tests."""

    def __init__(self):
        self.values = {}

    def get(self, operation):
        return self.values.get(operation)

    def set(self, operation, policy):
        self.values[operation] = policy

    def remove(self, operation):
        return self.values.pop(operation, None) is not None

    def all(self):
        return dict(self.values)


# --- @policy -------------------------------------------------------------------
class TestPolicy(unittest.TestCase):
    def _policy(self, char, actor, raw, switches=()):
        action = Policy.parse(raw, actor, switches=switches, verb="@policy")
        return dispatch(action, actor, [char])

    def test_sets_typed_policy(self):
        char, actor = _setup()
        target = FakeObj(key="bob")
        target.policies = FakePolicies()
        char.search_map["bob"] = target
        self._policy(char, actor, "bob/view = disabled", switches=("set",))
        self.assertEqual(target.policies.get("view").to_data()["type"], "never")
        self.assertTrue(any("bob/view" in message for message in char.messages))

    def test_control_required(self):
        char, actor = _setup()
        target = FakeObj(key="bob", access={"control": False, "edit": False})
        target.policies = FakePolicies()
        char.search_map["bob"] = target
        self._policy(char, actor, "bob/view = public", switches=("set",))
        self.assertIsNone(target.policies.get("view"))
        self.assertTrue(any("not allowed" in message for message in char.messages))


# --- @scope --------------------------------------------------------------------
class TestScope(unittest.TestCase):
    def _scope(self, char, actor, raw, switches=()):
        action = Scope.parse(raw, actor, switches=switches, verb="@scope")
        return dispatch(action, actor, [char])

    def test_sets_scope_labels(self):
        char, actor = _setup()
        target = FakeObj(key="bob")
        char.search_map["bob"] = target
        with mock.patch("evennia.authorization.storage.set_scope_labels") as setter:
            self._scope(
                char,
                actor,
                "bob = zone:market, project:renovation",
                switches=("set",),
            )
        setter.assert_called_once_with(target, {"zone:market", "project:renovation"})
        self.assertTrue(any("project:renovation" in message for message in char.messages))


# --- @access -------------------------------------------------------------------
class TestAccess(unittest.TestCase):
    def _access(self, char, actor):
        action = Access.parse("", actor, verb="@access")
        return dispatch(action, actor, [char])

    def test_shows_effective_capability_grants(self):
        char, actor = _setup(capabilities=())
        grants = SimpleNamespace(
            by_capability={"engine.world.build": (SimpleNamespace(kind="world", key="*"),)}
        )
        with mock.patch("evennia.authorization.storage.load_grants", return_value=grants):
            trace = self._access(char, actor)
        out = "\n".join(char.messages)
        self.assertIn("Your capability grants", out)
        self.assertIn("engine.world.build: world:*", out)
        self.assertEqual(trace.carry_out_gated, 0)

    def test_no_grants(self):
        char, actor = _setup(capabilities=())
        grants = SimpleNamespace(by_capability={})
        with mock.patch("evennia.authorization.storage.load_grants", return_value=grants):
            self._access(char, actor)
        self.assertTrue(any("<None>" in message for message in char.messages))


if __name__ == "__main__":
    unittest.main()
