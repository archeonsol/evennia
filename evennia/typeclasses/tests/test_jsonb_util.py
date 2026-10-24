"""
Tests for ``jsonb_util._is_json_safe``, the check that decides whether an Attribute's value is stored as it is or pickled.

A value is checked on every write of an Attribute, and a list of wound rows holds a few thousand values, so the check walks a
list or a dict in a loop that passes a plain value over with one type check and no call. The answer has to be the one the
recursive form gave, so these tests keep that form and hold the new one to it over made-up values of every kind.
"""

import random
import time

from django.test import SimpleTestCase

from evennia.typeclasses.jsonb_util import _is_json_safe


def recursive_is_json_safe(val):
    """The check as it was written before: a call and a generator for every value."""
    t = type(val)
    if t in (bool, type(None), str):
        return True
    if t is int or t is float:
        return True
    if t is list:
        return all(recursive_is_json_safe(v) for v in val)
    if t is dict:
        return all(isinstance(k, str) and recursive_is_json_safe(v) for k, v in val.items())
    return False


class Name(str):
    """A string that is not exactly a string."""


class Count(int):
    """An integer that is not exactly an integer."""


def made_up(rng, depth=0):
    """A random nested value, with every kind of thing that is and is not JSON-safe in it."""
    kinds = [
        "none", "bool", "int", "float", "str", "bytes", "tuple", "set", "name", "count", "list", "dict", "list", "dict",
    ]  # fmt: skip
    kind = rng.choice(kinds if depth < 4 else kinds[:11])
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "int":
        return rng.randint(-5, 10**12)
    if kind == "float":
        return rng.random() * 100
    if kind == "str":
        return "s%d" % rng.randint(0, 99)
    if kind == "bytes":
        return b"b"
    if kind == "tuple":
        return (1, 2)
    if kind == "set":
        return {1, 2}
    if kind == "name":
        return Name("n")
    if kind == "count":
        return Count(3)
    if kind == "list":
        return [made_up(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    keys = ["k%d" % rng.randint(0, 9), Name("subclass"), 5, None, ("a", 1)]
    weights = [12, 1, 1, 1, 1]
    return {
        rng.choices(keys, weights)[0]: made_up(rng, depth + 1) for _ in range(rng.randint(0, 4))
    }


def rows(count, keys=70):
    """A list of wound-like rows, each of `keys` scalar values and a few containers."""
    out = []
    for index in range(count):
        row = {"field_%d" % n: (n if n % 3 else "text") for n in range(keys)}
        row.update(limb_damage={"left_arm": 1}, tags=["a", "b"], index=index, fine=None)
        out.append(row)
    return out


class TestJsonSafeCheck(SimpleTestCase):
    def test_the_check_gives_the_answer_the_recursive_form_gave(self):
        rng = random.Random(20261024)
        answers = {True: 0, False: 0}
        for _ in range(3000):
            value = made_up(rng)
            expected = recursive_is_json_safe(value)
            answers[expected] += 1
            self.assertEqual(_is_json_safe(value), expected, value)
        # The made-up values reach both answers often, so the comparison above is not one-sided.
        self.assertGreater(answers[True], 200)
        self.assertGreater(answers[False], 200)

    def test_each_kind_of_scalar(self):
        for value in (None, True, False, 0, -3, 10**25, 1.5, "", "text"):
            self.assertTrue(_is_json_safe(value), value)
        for value in (b"x", (1,), {1}, Name("n"), Count(1), object()):
            self.assertFalse(_is_json_safe(value), value)

    def test_a_key_that_is_a_string_subclass_is_accepted_and_other_keys_are_not(self):
        self.assertTrue(_is_json_safe({Name("k"): 1}))
        self.assertFalse(_is_json_safe({1: 1}))
        self.assertFalse(_is_json_safe({None: 1}))
        self.assertFalse(_is_json_safe({("a",): 1}))

    def test_a_value_of_a_subclass_is_not_safe_inside_a_list_or_a_dict(self):
        self.assertFalse(_is_json_safe([Name("n")]))
        self.assertFalse(_is_json_safe({"k": Count(1)}))
        self.assertFalse(_is_json_safe({"k": [1, {"deep": (2,)}]}))

    def test_a_structure_that_contains_itself_fails_by_recursing_too_deep(self):
        loop = []
        loop.append(loop)
        with self.assertRaises(RecursionError):
            _is_json_safe(loop)
        mapping = {}
        mapping["self"] = mapping
        with self.assertRaises(RecursionError):
            _is_json_safe(mapping)

    def test_a_deep_nest_that_ends_is_safe(self):
        value = [1]
        for _ in range(200):
            value = {"inner": [value]}
        self.assertTrue(_is_json_safe(value))
        self.assertEqual(recursive_is_json_safe(value), True)

    def test_a_list_of_rows_is_checked_faster_than_the_recursive_form_checked_it(self):
        data = rows(25)

        def clock(check):
            best = None
            for _ in range(5):
                started = time.perf_counter()
                for _ in range(20):
                    check(data)
                elapsed = time.perf_counter() - started
                best = elapsed if best is None else min(best, elapsed)
            return best

        # Measured at about a fifth of the time; the test asks for less than three quarters so a busy machine cannot fail it.
        self.assertLess(clock(_is_json_safe), clock(recursive_is_json_safe) * 0.75)
