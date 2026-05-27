"""
Tests for evennia.utils.multimatch
"""

from django.test import TestCase, override_settings

from evennia.utils import multimatch


class TestParseMultimatchInput(TestCase):
    def test_first_prefix(self):
        sel, rem = multimatch.parse_multimatch_input("first sword")
        self.assertEqual(sel, 0)
        self.assertEqual(rem, "sword")

    def test_last_prefix(self):
        sel, rem = multimatch.parse_multimatch_input("last sword")
        self.assertEqual(sel, "last")
        self.assertEqual(rem, "sword")

    def test_other_prefix(self):
        sel, rem = multimatch.parse_multimatch_input("other sword")
        self.assertEqual(sel, "other")
        self.assertEqual(rem, "sword")

    @override_settings(
        SEARCH_MULTIMATCH_REGEX=r"^(?P<number>[0-9]+)-(?P<name>.*)(?P<args>(?:\s.*)?)$"
    )
    def test_numeric_prefix(self):
        sel, rem = multimatch.parse_multimatch_input("2-sword")
        self.assertEqual(sel, 1)
        self.assertEqual(rem, "sword")


class TestResolveMultimatchIndex(TestCase):
    def test_other_only_two(self):
        self.assertEqual(multimatch.resolve_multimatch_index("other", 2), 1)
        self.assertIsNone(multimatch.resolve_multimatch_index("other", 3))

    def test_last(self):
        self.assertEqual(multimatch.resolve_multimatch_index("last", 5), 4)


class TestMultimatchLabel(TestCase):
    def test_two_match_other(self):
        self.assertEqual(multimatch.multimatch_label(1, 2), "other")

    def test_three_match_last(self):
        self.assertEqual(multimatch.multimatch_label(2, 3), "last")


class TestParseLocationScope(TestCase):
    def test_my_scope(self):
        scope, rem = multimatch.parse_location_scope("my sword")
        self.assertEqual(scope, "inventory")
        self.assertEqual(rem, "sword")

    def test_no_false_positive(self):
        scope, rem = multimatch.parse_location_scope("mystery box")
        self.assertIsNone(scope)
        self.assertEqual(rem, "mystery box")


class TestParseSearchQualifiers(TestCase):
    def test_scope_then_ordinal(self):
        q = multimatch.parse_search_qualifiers("my first sword")
        self.assertEqual(q["scope"], "inventory")
        self.assertEqual(q["selector"], 0)
        self.assertEqual(q["searchdata"], "sword")
        self.assertTrue(q["had_qualifier"])


class TestTryAutopick(TestCase):
    class Caller:
        def __init__(self, loc):
            self.location = loc
            self.contents = []

    class Obj:
        def __init__(self, location):
            self.location = location

    def test_single_inventory(self):
        caller = self.Caller(loc=None)
        sword = self.Obj(caller)
        caller.contents = [sword]
        picked = multimatch.try_autopick([sword], caller)
        self.assertEqual(picked, sword)

    def test_one_each_no_pick(self):
        room = self.Caller(loc=None)
        caller = self.Caller(loc=room)
        inv_sword = self.Obj(caller)
        room_sword = self.Obj(room)
        picked = multimatch.try_autopick([inv_sword, room_sword], caller)
        self.assertIsNone(picked)
