"""Tests for ``ControlBinding`` — the durable control graph (I1).

Exercises the focus-stack semantics that replace ``session.puppet``: the
account floor, push/pop, ordered collapse (one entry dropped per nested
level), the generation race-guard counter, and reload reconstruction from the
persisted row.
"""

from evennia.accounts.models import ControlBinding
from evennia.utils.test_resources import EvenniaTest


class TestControlBinding(EvenniaTest):
    def _binding(self):
        return ControlBinding.objects.create(
            db_account=self.account, db_identity=self.char1
        )

    def test_floor_is_account_focus_never_none(self):
        b = self._binding()
        # Empty stack resolves to the account floor — focus is never None.
        self.assertEqual(b.focus, self.account)
        self.assertEqual(b.db_focus_stack[0], ["account", self.account.id])

    def test_push_makes_body_the_focus(self):
        b = self._binding()
        b.push(self.char1)
        self.assertEqual(b.focus, self.char1)
        self.assertTrue(b.contains(self.char1))

    def test_pop_returns_to_lower_body(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        self.assertEqual(b.focus, self.obj1)
        dropped = b.pop()
        self.assertEqual(dropped, self.obj1)
        self.assertEqual(b.focus, self.char1)

    def test_pop_never_below_floor(self):
        b = self._binding()
        self.assertIsNone(b.pop())  # only the account floor remains
        self.assertEqual(b.focus, self.account)

    def test_collapse_unwinds_top_first(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        b.push(self.obj2)
        # collapse back down to the character: obj2 then obj1, in unwind order.
        dropped = b.collapse_to(self.char1)
        self.assertEqual(dropped, [self.obj2, self.obj1])
        self.assertEqual(b.focus, self.char1)

    def test_collapse_to_missing_body_falls_to_floor(self):
        b = self._binding()
        b.push(self.char1)
        dropped = b.collapse_to(self.obj1)  # obj1 not in stack
        self.assertEqual(dropped, [self.char1])
        self.assertEqual(b.focus, self.account)

    def test_generation_bumps_on_each_mutation(self):
        b = self._binding()
        g0 = b.db_generation
        b.push(self.char1)
        self.assertEqual(b.db_generation, g0 + 1)
        b.push(self.obj1)
        self.assertEqual(b.db_generation, g0 + 2)
        b.pop()
        self.assertEqual(b.db_generation, g0 + 3)

    def test_collapse_is_a_single_generation_bump(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        b.push(self.obj2)
        g = b.db_generation
        b.collapse_to(self.char1)
        self.assertEqual(b.db_generation, g + 1)

    def test_reload_reconstructs_stack(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        gen = b.db_generation
        # Re-fetch from the DB — the persisted row alone rebuilds the graph.
        fresh = ControlBinding.objects.get(pk=b.pk)
        self.assertEqual(fresh.focus, self.obj1)
        self.assertEqual([o for o in fresh.stack_objects], [self.account, self.char1, self.obj1])
        self.assertEqual(fresh.db_generation, gen)
