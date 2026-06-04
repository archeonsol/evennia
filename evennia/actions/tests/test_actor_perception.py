"""Unit tests for the perception visibility seam applied by ``Actor.search``.

These exercise :mod:`evennia.actions.perception` and the filtering branch of
:meth:`evennia.actions.actor.Actor.search` with pure fakes (no DB): a fake
effective object whose ``search_for``/``search`` return canned results, and a
registered predicate that hides objects flagged ``hidden``.
"""

import unittest

from evennia.actions import perception
from evennia.actions.actor import Actor
from evennia.actions.exceptions import AmbiguousTarget
from evennia.objects.search_result import Ambiguous, Found, NotFound


class FakeNDB:
    pass


class Thing:
    def __init__(self, key, hidden=False):
        self.key = key
        self.hidden = hidden

    def __repr__(self):
        return f"Thing({self.key})"


class FakeEff:
    """Effective object: canned ``search_for`` + ``search`` hooks."""

    def __init__(self):
        self.ndb = FakeNDB()
        self.account = None
        self._for = lambda name, *a, **k: NotFound(name)
        self._sugar = lambda name, *a, **k: None

    def search_for(self, name, *args, **kwargs):
        return self._for(name, *args, **kwargs)

    def search(self, name, *args, **kwargs):
        return self._sugar(name, *args, **kwargs)


def _visible(searcher, candidate):
    return not getattr(candidate, "hidden", False)


class TestPerceptionFilter(unittest.TestCase):
    def setUp(self):
        perception.set_visibility_filter(_visible)
        self.eff = FakeEff()
        self.actor = Actor(character=self.eff)

    def tearDown(self):
        perception.set_visibility_filter(None)

    # -- module-level helpers ------------------------------------------------

    def test_default_is_permissive(self):
        perception.set_visibility_filter(None)
        self.assertTrue(perception.is_visible(self.eff, Thing("x", hidden=True)))

    def test_fail_open_on_predicate_error(self):
        perception.set_visibility_filter(lambda s, c: 1 / 0)
        self.assertTrue(perception.is_visible(self.eff, Thing("x")))

    def test_filter_visible_preserves_order(self):
        a, b, c = Thing("a"), Thing("b", hidden=True), Thing("c")
        self.assertEqual(perception.filter_visible(self.eff, [a, b, c]), [a, c])

    # -- ambiguity branch ----------------------------------------------------

    def test_ambiguous_all_visible_raises_with_candidates(self):
        cands = [Thing("goblin"), Thing("goblin chief")]
        self.eff._for = lambda name, *a, **k: Ambiguous(cands, name)
        with self.assertRaises(AmbiguousTarget) as ctx:
            self.actor.search("goblin")
        self.assertEqual(list(ctx.exception.candidates), cands)

    def test_ambiguous_one_hidden_resolves_to_visible(self):
        visible = Thing("goblin")
        cands = [visible, Thing("goblin chief", hidden=True)]
        self.eff._for = lambda name, *a, **k: Ambiguous(cands, name)
        # Only one candidate survives the filter → no prompt, resolve directly.
        self.assertIs(self.actor.search("goblin"), visible)

    def test_ambiguous_all_hidden_falls_through_to_sugar(self):
        cands = [Thing("a", hidden=True), Thing("b", hidden=True)]
        self.eff._for = lambda name, *a, **k: Ambiguous(cands, name)
        sentinel = object()
        self.eff._sugar = lambda name, *a, **k: sentinel
        # All candidates hidden: no prompt; defer to the game's sugar search.
        self.assertIs(self.actor.search("ghost"), sentinel)

    # -- single-result branch ------------------------------------------------

    def test_single_hidden_result_resolves_to_none(self):
        hidden = Thing("sneak", hidden=True)
        self.eff._for = lambda name, *a, **k: Found(hidden)
        self.eff._sugar = lambda name, *a, **k: hidden
        self.assertIsNone(self.actor.search("sneak"))

    def test_single_visible_result_returned(self):
        obj = Thing("lamp")
        self.eff._for = lambda name, *a, **k: Found(obj)
        self.eff._sugar = lambda name, *a, **k: obj
        self.assertIs(self.actor.search("lamp"), obj)

    def test_list_result_is_filtered(self):
        a, b = Thing("a"), Thing("b", hidden=True)
        self.eff._for = lambda name, *a_, **k: NotFound(name)
        self.eff._sugar = lambda name, *a_, **k: [a, b]
        self.assertEqual(self.actor.search("things"), [a])

    def test_self_is_always_visible(self):
        # A predicate that hides everything still lets the searcher resolve self.
        perception.set_visibility_filter(
            lambda searcher, candidate: candidate is searcher
        )
        self.eff._for = lambda name, *a, **k: Found(self.eff)
        self.eff._sugar = lambda name, *a, **k: self.eff
        self.assertIs(self.actor.search("me"), self.eff)
