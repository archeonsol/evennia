"""Unit tests for the typed SearchResult variants.

These tests don't touch the DB; the result types are pure dataclasses.
"""

import unittest

from evennia.objects.search_result import Ambiguous, Found, NotFound, SearchResult


class TestSearchResult(unittest.TestCase):
    def test_found_is_truthy(self):
        self.assertTrue(Found(obj=object()))

    def test_ambiguous_is_falsy(self):
        self.assertFalse(Ambiguous(candidates=[object(), object()], search_string="x"))

    def test_notfound_is_falsy(self):
        self.assertFalse(NotFound(search_string="x"))

    def test_found_carries_obj(self):
        sentinel = object()
        result = Found(obj=sentinel)
        self.assertIs(result.obj, sentinel)
        self.assertIsNone(result.stack)

    def test_found_with_stack(self):
        items = [object(), object(), object()]
        result = Found(obj=items[0], stack=items)
        self.assertIs(result.obj, items[0])
        self.assertEqual(result.stack, items)

    def test_ambiguous_fields(self):
        cands = [object(), object()]
        result = Ambiguous(candidates=cands, search_string="sword")
        self.assertEqual(result.candidates, cands)
        self.assertEqual(result.search_string, "sword")
        self.assertFalse(result.invalid_other)

    def test_ambiguous_invalid_other(self):
        result = Ambiguous(candidates=[1, 2, 3], search_string="x", invalid_other=True)
        self.assertTrue(result.invalid_other)

    def test_notfound_carries_query(self):
        result = NotFound(search_string="missing")
        self.assertEqual(result.search_string, "missing")

    def test_pattern_matching(self):
        """Each variant is matchable via Python pattern matching."""
        for result, expected in [
            (Found(obj="x"), "found"),
            (Ambiguous(candidates=[1, 2], search_string="q"), "ambiguous"),
            (NotFound(search_string="q"), "notfound"),
        ]:
            match result:
                case Found():
                    label = "found"
                case Ambiguous():
                    label = "ambiguous"
                case NotFound():
                    label = "notfound"
                case _:
                    label = "other"
            self.assertEqual(label, expected)

    def test_subclass_of_base(self):
        for cls in (Found, Ambiguous, NotFound):
            self.assertTrue(issubclass(cls, SearchResult))

    def test_equality(self):
        a = object()
        self.assertEqual(Found(obj=a), Found(obj=a))
        self.assertNotEqual(Found(obj=a), Found(obj=object()))
        self.assertEqual(NotFound(search_string="x"), NotFound(search_string="x"))
        self.assertNotEqual(NotFound(search_string="x"), NotFound(search_string="y"))


if __name__ == "__main__":
    unittest.main()
