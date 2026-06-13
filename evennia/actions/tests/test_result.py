"""Tests for RuleResult + trace types and action exceptions (CM1 Phase 0d/0e)."""

import unittest

from evennia.actions.exceptions import (
    ActionError,
    AmbiguousTarget,
    ParseError,
    RuleConflict,
)
from evennia.actions.result import (
    CLAIM,
    FAIL,
    PASS,
    REDIRECT,
    SILENT_FAIL,
    SKIP,
    ActionTrace,
    PhaseTrace,
    RuleResult,
)


class TestRuleResult(unittest.TestCase):
    def test_singletons_frozen(self):
        with self.assertRaises(Exception):
            PASS.kind = "fail"  # frozen

    def test_kinds(self):
        self.assertTrue(PASS.is_pass)
        self.assertTrue(SKIP.is_skip)
        self.assertTrue(CLAIM.is_claim)
        self.assertTrue(SILENT_FAIL.blocks)

    def test_fail_carries_message(self):
        r = FAIL("nope")
        self.assertEqual(r.kind, "fail")
        self.assertEqual(r.message, "nope")
        self.assertTrue(r.blocks)

    def test_redirect_carries_action(self):
        sentinel = object()
        r = REDIRECT(sentinel)
        self.assertTrue(r.is_redirect)
        self.assertIs(r.redirect_to, sentinel)

    def test_redirect_to_excluded_from_eq(self):
        # two redirects compare equal despite different targets (redirect_to is
        # compare=False) — and remain hashable even with an unhashable target.
        self.assertEqual(REDIRECT([1, 2]), REDIRECT([3, 4]))
        hash(REDIRECT([1, 2]))  # must not raise

    def test_silent_fail_no_message(self):
        self.assertIsNone(SILENT_FAIL.message)
        self.assertFalse(SILENT_FAIL.is_pass)


class TestTrace(unittest.TestCase):
    def test_record_and_filter(self):
        trace = ActionTrace(action=object(), actor_key="char#1")
        trace.record(PhaseTrace("check", "Ball#34", 34, "check_kick", PASS, 12))
        trace.record(PhaseTrace("carry_out", "Ball#34", 34, "carry_out_kick", CLAIM, 30))
        self.assertEqual(len(trace.phases), 2)
        self.assertEqual(len(trace.for_phase("check")), 1)
        self.assertEqual(trace.for_phase("carry_out")[0].result, CLAIM)

    def test_defaults(self):
        trace = ActionTrace(action=object(), actor_key="x")
        self.assertEqual(trace.outcome, "no_rules")
        self.assertEqual(trace.phases, [])
        self.assertEqual(trace.redirect_count, 0)


class TestExceptions(unittest.TestCase):
    def test_hierarchy(self):
        for exc in (RuleConflict, ParseError, AmbiguousTarget):
            self.assertTrue(issubclass(exc, ActionError))

    def test_parse_error_suggestions(self):
        e = ParseError("bad", suggestions=["look", "loot"])
        self.assertEqual(e.message, "bad")
        self.assertEqual(e.suggestions, ["look", "loot"])

    def test_ambiguous_target(self):
        cands = [object(), object()]
        e = AmbiguousTarget(cands, "ball")
        self.assertEqual(e.candidates, cands)
        self.assertEqual(e.original_raw, "ball")


if __name__ == "__main__":
    unittest.main()
