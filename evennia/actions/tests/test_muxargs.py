"""Tests for the mux-style action argument parser (``evennia.actions.muxargs``).

The helper reproduces ``Command.parse``'s lhs/rhs/lhslist/rhslist split and
``ObjManipCommand.parse``'s per-objdef alias/option breakdown, for actions that
host former mux/objmanip commands. These are pure-function tests: no DB, no
registry.
"""

import unittest
from dataclasses import dataclass

from evennia.actions.muxargs import ArgAction, mux_parse


class TestMuxParse(unittest.TestCase):
    def test_empty(self):
        result = mux_parse("")
        self.assertEqual(result.args, "")
        self.assertEqual(result.arglist, [])
        self.assertEqual(result.lhs, "")
        self.assertIsNone(result.rhs)
        # mirrors Command.parse: "".split(",") -> [""]
        self.assertEqual(result.lhslist, [""])
        self.assertEqual(result.rhslist, [])

    def test_none_input(self):
        # None coerces to "", identical to the empty-string case.
        self.assertEqual(mux_parse(None), mux_parse(""))

    def test_no_rhs(self):
        result = mux_parse("  sword  ")
        self.assertEqual(result.args, "sword")
        self.assertEqual(result.lhs, "sword")
        self.assertIsNone(result.rhs)
        self.assertEqual(result.lhslist, ["sword"])
        self.assertEqual(result.rhslist, [])

    def test_arglist_whitespace_split(self):
        self.assertEqual(mux_parse("give sword to bob").arglist, ["give", "sword", "to", "bob"])

    def test_lhs_rhs_split(self):
        result = mux_parse("key = chest")
        self.assertEqual(result.lhs, "key")
        self.assertEqual(result.rhs, "chest")

    def test_only_first_equals_splits(self):
        result = mux_parse("a = b = c")
        self.assertEqual(result.lhs, "a")
        self.assertEqual(result.rhs, "b = c")

    def test_comma_lists(self):
        result = mux_parse("a, b ,c = x, y")
        self.assertEqual(result.lhslist, ["a", "b", "c"])
        self.assertEqual(result.rhslist, ["x", "y"])

    def test_empty_rhs_is_str_not_none(self):
        # "x =" yields an explicit (empty) rhs, distinct from "x" (no rhs).
        result = mux_parse("x =")
        self.assertEqual(result.rhs, "")
        self.assertEqual(result.rhslist, [""])

    def test_objdef_aliases_and_option(self):
        result = mux_parse("box;crate;chest:container = ")
        self.assertEqual(
            result.lhs_objs,
            [{"name": "box", "option": "container", "aliases": ["crate", "chest"]}],
        )

    def test_objdef_option_only(self):
        result = mux_parse("door:north")
        self.assertEqual(
            result.lhs_objs,
            [{"name": "door", "option": "north", "aliases": []}],
        )

    def test_objdef_plain_name(self):
        result = mux_parse("apple")
        self.assertEqual(
            result.lhs_objs,
            [{"name": "apple", "option": None, "aliases": []}],
        )

    def test_objdef_both_sides(self):
        result = mux_parse("a;al = b:opt")
        self.assertEqual(result.lhs_objs, [{"name": "a", "option": None, "aliases": ["al"]}])
        self.assertEqual(result.rhs_objs, [{"name": "b", "option": "opt", "aliases": []}])

    def test_option_split_is_rightmost(self):
        # rsplit(":", 1): only the final colon delimits the option.
        result = mux_parse("a:b:c")
        self.assertEqual(result.lhs_objs, [{"name": "a:b", "option": "c", "aliases": []}])

    def test_custom_single_delimiter(self):
        result = mux_parse("a to b", rhs_split=" to ")
        self.assertEqual(result.lhs, "a")
        self.assertEqual(result.rhs, "b")

    def test_iterable_delimiters_first_present_wins(self):
        # "=" is absent, " to " is present -> split on " to ".
        result = mux_parse("a to b", rhs_split=("=", " to "))
        self.assertEqual(result.lhs, "a")
        self.assertEqual(result.rhs, "b")

    def test_iterable_delimiters_order(self):
        # both present; first listed that appears wins (order, not position).
        result = mux_parse("a = b to c", rhs_split=("=", " to "))
        self.assertEqual(result.lhs, "a")
        self.assertEqual(result.rhs, "b to c")


class TestArgAction(unittest.TestCase):
    """The default action shape for mux/objmanip-style verbs: ``parse`` carries
    the full :func:`mux_parse` breakdown plus switches and the matched verb."""

    def test_parse_carries_mux_breakdown(self):
        act = ArgAction.parse("box;crate:container = chest, sack", None)
        self.assertEqual(act.args, "box;crate:container = chest, sack")
        self.assertEqual(act.lhs, "box;crate:container")
        self.assertEqual(act.rhs, "chest, sack")
        self.assertEqual(act.rhslist, ("chest", "sack"))
        self.assertEqual(
            act.lhs_objs,
            ({"name": "box", "option": "container", "aliases": ["crate"]},),
        )

    def test_parse_carries_switches_and_verb(self):
        act = ArgAction.parse("x = y", None, switches=("del", "quiet"), verb="@perm")
        self.assertEqual(act.switches, ("del", "quiet"))
        self.assertEqual(act.verb, "@perm")

    def test_no_rhs_is_none(self):
        act = ArgAction.parse("sword", None)
        self.assertEqual(act.lhs, "sword")
        self.assertIsNone(act.rhs)
        self.assertEqual(act.rhslist, ())

    def test_empty_args(self):
        act = ArgAction.parse("", None)
        self.assertEqual(act.args, "")
        self.assertEqual(act.lhs, "")
        self.assertIsNone(act.rhs)

    def test_subclass_rhs_split_override(self):
        @dataclass
        class _To(ArgAction):
            rhs_split = ("=", " to ")

        act = _To.parse("a to b", None)
        self.assertEqual(act.lhs, "a")
        self.assertEqual(act.rhs, "b")


if __name__ == "__main__":
    unittest.main()
