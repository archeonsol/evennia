"""Tests for @action / @rule / registries (CM1 Phase 0b/0c)."""

import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from evennia.actions.action import Action, action
from evennia.actions.exceptions import RuleConflict
from evennia.actions.predicate import HasCapability
from evennia.actions.registry import ActionRegistry, RuleRegistry
from evennia.actions.result import CLAIM, FAIL, PASS
from evennia.actions.rule import PHASES, RuleSpec, rule


# --- test action types (registered on a private registry where possible) ----
@dataclass
class _Kick(Action):
    target: object = None


@dataclass
class _Look(Action):
    target: object = None


class TestActionRegistry(unittest.TestCase):
    def test_register_and_get(self):
        reg = ActionRegistry()
        reg.register(_Kick, ("kick", "boot"))
        self.assertIs(reg.get("kick"), _Kick)
        self.assertIs(reg.get("BOOT"), _Kick)  # case-insensitive
        self.assertIsNone(reg.get("nope"))

    def test_duplicate_verb_raises(self):
        reg = ActionRegistry()
        reg.register(_Kick, ("kick",))
        with self.assertRaises(RuleConflict):
            reg.register(_Look, ("kick",))

    def test_same_class_reregister_ok(self):
        reg = ActionRegistry()
        reg.register(_Kick, ("kick",))
        reg.register(_Kick, ("kick", "boot"))  # idempotent / additive
        self.assertEqual(reg.get("boot"), _Kick)
        self.assertEqual(len(reg.all_actions()), 1)

    def test_verbs_for_includes_supplemental_and_omits_override(self):
        reg = ActionRegistry()

        class EngineAction(Action):
            pass

        class GameAction(Action):
            pass

        EngineAction.__module__ = "evennia.actions.default.test_registry"
        GameAction.__module__ = "world.actions.test_registry"
        reg.register(EngineAction, ("engine-primary",))
        reg.register(EngineAction, ("engine-extra",))
        reg.register(GameAction, ("engine-primary",))

        self.assertEqual(reg.verbs_for(EngineAction), ("engine-extra",))
        self.assertEqual(reg.verbs_for(GameAction), ("engine-primary",))

    def test_match_tokens_longest_phrase(self):
        reg = ActionRegistry()

        @dataclass
        class _Go(Action):
            pass

        @dataclass
        class _GoShard(Action):
            pass

        reg.register(_Go, ("go",))
        reg.register(_GoShard, ("go shard",))
        self.assertEqual(reg._max_phrase_words, 2)
        self.assertIn("go", reg._multi_word_starters)

        vm = reg.match_tokens(["go", "shard"])
        self.assertIsNotNone(vm)
        self.assertEqual(vm.canonical, "go shard")
        self.assertIs(vm.action_cls, _GoShard)
        self.assertEqual(vm.span, 2)

        vm_go = reg.match_tokens(["go", "north"])
        self.assertIs(vm_go.action_cls, _Go)
        self.assertEqual(vm_go.span, 1)

    def test_match_verb_wraps_match_tokens(self):
        reg = ActionRegistry()
        reg.register(_Kick, ("kick",))
        matched = reg.match_verb("kick")
        self.assertEqual(matched, ("kick", _Kick, 1.0))

    def test_action_decorator_sets_verbs(self):
        @action("frobnicate", "frob")
        @dataclass
        class _Frob(Action):
            pass

        self.assertEqual(_Frob.__action_verbs__, ("frobnicate", "frob"))

    def test_action_requires_verb(self):
        with self.assertRaises(ValueError):

            @action()
            @dataclass
            class _Bad(Action):
                pass


class TestActionBase(unittest.TestCase):
    def test_catch_all_marker_not_inherited(self):
        self.assertIn("_is_catch_all_base", Action.__dict__)
        self.assertNotIn("_is_catch_all_base", _Kick.__dict__)

    def test_subclass_required_field_ok(self):
        # base private fields are init=False, so a required subclass field works
        @dataclass(kw_only=True)
        class _Need(Action):
            target: object

        inst = _Need(target="ball")
        self.assertEqual(inst.target, "ball")
        self.assertEqual(inst._raw_string, "")
        self.assertIsNone(inst._actor)


class TestRuleDecorator(unittest.TestCase):
    def test_attaches_specs(self):
        requirement = HasCapability("engine.world.build")

        @rule(_Kick, phase="check", priority=5, requires=requirement)
        def my_rule(self, action, actor):
            return PASS

        specs = my_rule.__evennia_rule_specs__
        self.assertEqual(len(specs), 1)
        s = specs[0]
        self.assertEqual(s.action_type, _Kick)
        self.assertEqual(s.phase, "check")
        self.assertEqual(s.priority, 5)
        self.assertIs(s.requires, requirement)

    def test_tuple_expands(self):
        @rule((_Kick, _Look), phase="report")
        def r(self, action, actor):
            return PASS

        specs = r.__evennia_rule_specs__
        self.assertEqual({s.action_type for s in specs}, {_Kick, _Look})

    def test_stacking(self):
        @rule(_Kick, phase="check")
        @rule(_Look, phase="carry_out")
        def r(self, action, actor):
            return PASS

        self.assertEqual(len(r.__evennia_rule_specs__), 2)

    def test_bad_phase(self):
        with self.assertRaises(ValueError):

            @rule(_Kick, phase="nonsense")
            def r(self, action, actor):
                return PASS

    def test_requires_compiled_once_to_predicate(self):
        @rule(_Kick, phase="check", requires="engine.runtime.manage")
        def r(self, action, actor):
            return PASS

        self.assertIsInstance(r.__evennia_rule_specs__[0].requires, HasCapability)

    def test_requires_lockstring_rejected(self):
        with self.assertRaises(ValueError):

            @rule(_Kick, phase="check", requires="perm(Builder)")
            def r(self, action, actor):
                return PASS


class TestRuleRegistry(unittest.TestCase):
    def _provider(self):
        class Provider:
            @rule(_Kick, phase="check", priority=1)
            def check_low(self, action, actor):
                return PASS

            @rule(_Kick, phase="check", priority=10)
            def check_high(self, action, actor):
                return PASS

            @rule(_Kick, phase="carry_out")
            def do_it(self, action, actor):
                return CLAIM

            @rule(Action, phase="check", priority=999)
            def gate_all(self, action, actor):
                return PASS

        return Provider

    def test_collect_indexes_concrete_and_catchall(self):
        reg = RuleRegistry()
        Provider = self._provider()
        reg.collect(Provider)
        self.assertIn((_Kick, "check"), Provider.__evennia_rules__)
        self.assertIn("check", Provider.__evennia_catchall__)

    def test_rules_for_merges_catchall_priority_sorted(self):
        reg = RuleRegistry()
        Provider = self._provider()
        merged = reg.rules_for(Provider, _Kick, "check")
        names = [s.rule_name for s in merged]
        # catch-all (priority 999) first, then check_high (10), then check_low (1)
        self.assertEqual(names, ["gate_all", "check_high", "check_low"])

    def test_rules_for_catchall_applies_to_unnamed_action(self):
        reg = RuleRegistry()
        Provider = self._provider()
        merged = reg.rules_for(Provider, _Look, "check")  # no concrete _Look rule
        self.assertEqual([s.rule_name for s in merged], ["gate_all"])

    def test_rules_for_memoized(self):
        reg = RuleRegistry()
        Provider = self._provider()
        first = reg.rules_for(Provider, _Kick, "check")
        second = reg.rules_for(Provider, _Kick, "check")
        self.assertIs(first, second)

    def test_override_shadows_base_rule(self):
        class Base:
            @rule(_Kick, phase="check")
            def gate(self, action, actor):
                return FAIL("base")

        class Sub(Base):
            # override without @rule removes the rule
            def gate(self, action, actor):
                return PASS

        reg = RuleRegistry()
        merged = reg.rules_for(Sub, _Kick, "check")
        self.assertEqual(merged, [])

    def test_all_specs_introspection(self):
        reg = RuleRegistry()
        Provider = self._provider()
        specs = reg.all_specs(Provider)
        self.assertEqual(len(specs), 4)
        self.assertTrue(all(isinstance(s, RuleSpec) for s in specs))


class TestSuggestVerbs(unittest.TestCase):
    """``suggest_verbs`` filters candidates *during* the nearest-first walk, so
    a rejected near candidate does not crowd a reachable farther one out of the
    ``limit`` slice."""

    def _registry(self):
        reg = ActionRegistry()
        for verb in ("casa", "case", "cash", "mast"):

            @dataclass
            class _A(Action):
                pass

            reg.register(_A, (verb,))
        return reg

    def test_unfiltered_returns_closest_first(self):
        reg = self._registry()
        self.assertEqual(reg.suggest_verbs("cast"), ["casa", "case", "cash"])

    def test_filter_fills_limit_from_remaining_candidates(self):
        reg = self._registry()
        rejected = {"casa", "case"}
        out = reg.suggest_verbs("cast", reachable=lambda v, cls: v not in rejected)
        self.assertEqual(out, ["cash", "mast"])

    def test_filter_rejecting_all_yields_empty(self):
        reg = self._registry()
        self.assertEqual(reg.suggest_verbs("cast", reachable=lambda v, cls: False), [])

    def test_system_verbs_never_suggested(self):
        reg = self._registry()
        reg.register(_Kick, ("__cast__",))
        self.assertNotIn("__cast__", reg.suggest_verbs("__cast__"))


class TestPhasesConstant(unittest.TestCase):
    def test_phases(self):
        self.assertEqual(PHASES, ("before", "check", "carry_out", "report"))


if __name__ == "__main__":
    unittest.main()
