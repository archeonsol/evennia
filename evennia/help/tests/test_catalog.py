"""Tests for access-aware action help catalog."""

from types import SimpleNamespace
from unittest import TestCase

from evennia.actions import Action, Builder, action, rule
from evennia.actions.registry import rule_registry
from evennia.actions.tests.test_permission import _Attrs, _Perms
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
    @rule(_StaffAction, phase="carry_out", requires=Builder)
    def staff_carry(self, action, actor):
        pass

    @rule(_PlayAction, phase="carry_out")
    def play_carry(self, action, actor):
        pass


rule_registry.collect(_TestCharProvider)


class TestHelpCatalog(TestCase):
    def _actor(self, account_perms, char_perms=()):
        acct = SimpleNamespace(
            permissions=_Perms(account_perms),
            attributes=_Attrs(),
            is_superuser=False,
        )
        char = _TestCharProvider()
        char.account = acct
        char.permissions = _Perms(char_perms)
        from evennia.actions.actor import Actor

        return Actor(account=acct, character=char)

    def test_resolve_help_category_prefix(self):
        register_help_category_prefix("evennia.help.tests", "TestCat")
        self.assertEqual(resolve_help_category(_PlayAction), "TestCat")

    def test_player_sees_play_not_staff(self):
        actor = self._actor(["Player"], ["Player"])
        topics = collect_action_help_topics(actor, staff_reference=True)
        self.assertIn("playverb", topics)
        self.assertNotIn("staffverb", topics)

    def test_player_index_omits_actions(self):
        actor = self._actor(["Player"], ["Player"])
        topics = collect_action_help_topics(actor, mode="list")
        self.assertEqual(topics, {})

    def test_builder_sees_staff(self):
        actor = self._actor(["Developer"], ["Builder"])
        topics = collect_action_help_topics(actor, mode="list")
        self.assertIn("staffverb", topics)

    def test_denied_lookup_for_staff_reference(self):
        actor = self._actor(["Player"], ["Player"])
        topic, denied = lookup_action_help_topic(
            "staffverb", actor, include_denied=True, staff_reference=True
        )
        self.assertTrue(denied)
        self.assertIsNotNone(topic)
        self.assertFalse(action_help_accessible(_StaffAction, actor, staff_reference=True))

    def test_player_cannot_lookup_action_topics(self):
        actor = self._actor(["Player"], ["Player"])
        topic, denied = lookup_action_help_topic("staffverb", actor, include_denied=True)
        self.assertFalse(denied)
        self.assertIsNone(topic)

    def test_open_action_has_no_requirement_desc(self):
        actor = self._actor(["Player"], ["Player"])
        self.assertIsNone(action_help_requirement_desc(_PlayAction, actor))

    def test_staff_action_requirement_desc_for_player(self):
        actor = self._actor(["Player"], ["Player"])
        desc = action_help_requirement_desc(_StaffAction, actor)
        self.assertIsNotNone(desc)
        self.assertIn("builder", desc.lower())
