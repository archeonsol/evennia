"""Tests for the mux-style action argument parser (``evennia.actions.muxargs``).

The helper reproduces ``Command.parse``'s lhs/rhs/lhslist/rhslist split and
``ObjManipCommand.parse``'s per-objdef alias/option breakdown, for actions that
host former mux/objmanip commands. These are pure-function tests: no DB, no
registry.
"""

import unittest

from evennia.actions.muxargs import mux_parse


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


if __name__ == "__main__":
    unittest.main()
