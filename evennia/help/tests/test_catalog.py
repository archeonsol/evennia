"""Tests for access-aware action help catalog."""

from unittest import TestCase

from django.test import override_settings

from evennia.actions import Action, HasCapability, action, rule
from evennia.actions.registry import rule_registry
from evennia.help.catalog import (
    action_help_accessible,
    action_help_requirement_desc,
    collect_action_help_topics,
    lookup_action_help_topic,
    register_help_category_prefix,
    resolve_help_category,
)


@action("staffverb", help_category="Building")
class _StaffAction(Action):
    """Staff-only test action."""


@action("playverb")
class _PlayAction(Action):
    """Player test action."""


class _TestCharProvider:
    def __init__(self, capabilities=()):
        self.capabilities = frozenset(capabilities)
        self.account = None

    def has_capability(self, capability, **kwargs):
        """Return whether this test principal has a capability."""
        return capability in self.capabilities

    @rule(
        _StaffAction,
        phase="carry_out",
        requires=HasCapability("engine.world.build"),
    )
    def staff_carry(self, action, actor):
        pass

    @rule(_PlayAction, phase="carry_out")
    def play_carry(self, action, actor):
        pass


rule_registry.collect(_TestCharProvider)


class TestHelpCatalog(TestCase):
    def _actor(self, char_capabilities=()):
        acct = _TestCharProvider()
        char = _TestCharProvider(char_capabilities)
        char.account = acct
        from evennia.actions.actor import Actor

        return Actor(account=acct, character=char)

    def test_resolve_help_category_prefix(self):
        register_help_category_prefix("evennia.help.tests", "TestCat")
        self.assertEqual(resolve_help_category(_PlayAction), "TestCat")

    def test_player_sees_play_not_staff(self):
        actor = self._actor()
        topics = collect_action_help_topics(actor, staff_reference=True)
        self.assertIn("playverb", topics)
        self.assertNotIn("staffverb", topics)

    def test_player_index_omits_actions(self):
        actor = self._actor()
        topics = collect_action_help_topics(actor, mode="list")
        self.assertEqual(topics, {})

    @override_settings(HELP_INDEX_ACTIONS=True)
    def test_authorized_actor_sees_staff(self):
        actor = self._actor(("engine.world.build",))
        topics = collect_action_help_topics(actor, mode="list")
        self.assertIn("staffverb", topics)

    def test_denied_lookup_for_staff_reference(self):
        actor = self._actor()
        topic, denied = lookup_action_help_topic(
            "staffverb", actor, include_denied=True, staff_reference=True
        )
        self.assertTrue(denied)
        self.assertIsNotNone(topic)
        self.assertFalse(action_help_accessible(_StaffAction, actor, staff_reference=True))

    def test_player_cannot_lookup_action_topics(self):
        actor = self._actor()
        topic, denied = lookup_action_help_topic("staffverb", actor, include_denied=True)
        self.assertFalse(denied)
        self.assertIsNone(topic)

    def test_open_action_has_no_requirement_desc(self):
        actor = self._actor()
        self.assertIsNone(action_help_requirement_desc(_PlayAction, actor))

    def test_staff_action_requirement_desc_for_player(self):
        actor = self._actor()
        desc = action_help_requirement_desc(_StaffAction, actor)
        self.assertIsNotNone(desc)
        self.assertIn("engine.world.build", desc)
