"""Tests for access-aware action help catalog."""

from unittest import TestCase, mock

from django.test import override_settings

from evennia.actions import Action, Actor, HasCapability, action, rule
from evennia.actions.registry import action_registry, rule_registry
from evennia.help import catalog as catalog_module
from evennia.help.catalog import (
    _provider_types,
    action_help_accessible,
    action_help_requirement_desc,
    carry_out_specs_for,
    collect_action_help_topics,
    lookup_action_help_topic,
    register_help_category_prefix,
    resolve_help_category,
)


@action("staffverb", "staffverb-alias", help_category="Building")
class _StaffAction(Action):
    """Staff-only test action."""


@action("playverb")
class _PlayAction(Action):
    """Player test action."""


@action("@catalog-prefixed")
class _PrefixedAction(Action):
    """Prefix-sensitive test action."""


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


@action("mro-help-action")
class _MroHelpAction(Action):
    """Action used to prove inherited rule lookup semantics."""


class _MroBaseProvider:
    @rule(_MroHelpAction, phase="carry_out", priority=10)
    def carry_out_mro_concrete(self, action, actor):
        """Provide one concrete inherited rule."""

    @rule(Action, phase="carry_out", priority=20)
    def carry_out_mro_catchall(self, action, actor):
        """Provide one inherited catch-all rule."""


class _MroDerivedProvider(_MroBaseProvider):
    pass


class _ShadowBaseProvider:
    @rule(_MroHelpAction, phase="carry_out")
    def carry_out_shadowed(self, action, actor):
        """Provide a rule that a derived plain method suppresses."""


class _ShadowDerivedProvider(_ShadowBaseProvider):
    def carry_out_shadowed(self, action, actor):
        """Suppress the inherited decorated method."""


class _NonReflexiveProviderMeta(type):
    """Model a provider metaclass whose class equality is not identity."""

    def __eq__(cls, other):
        return False

    __hash__ = type.__hash__


class _NonReflexiveProvider(metaclass=_NonReflexiveProviderMeta):
    pass


class _EngineOwnedAction(Action):
    """Engine-shaped action whose alias is overridden downstream."""


class _GameOverrideAction(Action):
    """Game-shaped action overriding one engine verb."""


_EngineOwnedAction.__module__ = "evennia.actions.default.test_catalog"
_EngineOwnedAction.__action_verbs__ = ("catalog-engine", "catalog-rebound")
_GameOverrideAction.__module__ = "world.actions.test_catalog"
_GameOverrideAction.__action_verbs__ = ("catalog-rebound",)
action_registry.register(_EngineOwnedAction, _EngineOwnedAction.__action_verbs__)
action_registry.register(_EngineOwnedAction, ("catalog-supplemental",))
action_registry.register(_GameOverrideAction, _GameOverrideAction.__action_verbs__)


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

    def test_exact_alias_lookup_does_not_enumerate_or_prefix_match(self):
        actor = self._actor(("engine.world.build",))
        with mock.patch.object(
            catalog_module,
            "_iter_action_verbs",
            side_effect=AssertionError("exact lookup enumerated the action registry"),
        ):
            topic, denied = lookup_action_help_topic(
                "staffverb-alias", actor, include_denied=True, staff_reference=True
            )
            prefix_topic, prefix_denied = lookup_action_help_topic(
                "staffverb-a", actor, include_denied=True, staff_reference=True
            )

        self.assertFalse(denied)
        self.assertEqual(topic.key, "staffverb-alias")
        self.assertIs(topic.action_cls, _StaffAction)
        self.assertIn("staffverb", topic.aliases)
        self.assertIsNone(prefix_topic)
        self.assertFalse(prefix_denied)

    def test_exact_lookup_does_not_strip_at_prefix(self):
        actor = self._actor()

        exact, _ = lookup_action_help_topic(
            "@catalog-prefixed", actor, include_denied=True, staff_reference=True
        )
        stripped, denied = lookup_action_help_topic(
            "catalog-prefixed", actor, include_denied=True, staff_reference=True
        )

        self.assertIsNotNone(exact)
        self.assertIsNone(stripped)
        self.assertFalse(denied)

    def test_exact_topic_omits_alias_now_owned_by_override(self):
        actor = self._actor()

        topic, denied = lookup_action_help_topic(
            "catalog-engine", actor, include_denied=True, staff_reference=True
        )

        self.assertTrue(denied)
        self.assertIs(topic.action_cls, _EngineOwnedAction)
        self.assertNotIn("catalog-rebound", topic.aliases)
        self.assertIn("catalog-supplemental", topic.aliases)

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

    def test_carry_out_specs_use_mro_cache_once_with_priority(self):
        specs = carry_out_specs_for(_MroDerivedProvider, _MroHelpAction)

        self.assertEqual(
            [spec.rule_name for spec in specs],
            ["carry_out_mro_catchall", "carry_out_mro_concrete"],
        )

    def test_plain_derived_override_suppresses_decorated_base_rule(self):
        specs = carry_out_specs_for(_ShadowDerivedProvider, _MroHelpAction)

        self.assertEqual(specs, [])

    def test_provider_type_deduplication_uses_identity(self):
        provider = _NonReflexiveProvider()
        actor = Actor(account=provider)

        self.assertEqual(_provider_types(actor, _MroHelpAction), (_NonReflexiveProvider,))
