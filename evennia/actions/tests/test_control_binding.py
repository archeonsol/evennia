"""Tests for ``ControlBinding`` — the durable control graph (I1).

Exercises the focus-stack semantics that replace ``session.puppet``: the
derived controller floor (F2 — the floor is never stored), push/pop, ordered
collapse (one entry dropped per nested level), the generation race-guard
counter, the controller-vs-ownership split, and the idmapper sharing that lets
co-sessions see each other's push/pop with no refresh.
"""

from evennia.accounts.models import ControlBinding
from evennia.utils import create
from evennia.utils.test_resources import EvenniaTest


class TestControlBinding(EvenniaTest):
    def _binding(self, account=None):
        # Anchor a fresh, unpuppeted identity. setUp logs in self.account, which
        # auto-binds self.char1; reusing char1 here collides on the OneToOne
        # db_identity.
        account = account or self.account
        identity = create.create_object(self.character_typeclass, key="BoundChar")
        return ControlBinding.objects.create(db_account=account, db_identity=identity)

    # -- F2 focus stack (floor is derived, never stored) --------------------
    def test_empty_stack_focus_is_controller(self):
        b = self._binding()
        # OOC: nothing pushed, focus rests on the controller account (derived).
        self.assertEqual(b.db_focus_stack, [])
        self.assertEqual(b.focus, self.account)

    def test_push_makes_body_the_focus(self):
        b = self._binding()
        b.push(self.char1)
        self.assertEqual(b.focus, self.char1)
        self.assertTrue(b.contains(self.char1))
        # the floor is NOT in the stored stack.
        self.assertEqual(b.db_focus_stack, [["object", self.char1.id]])

    def test_pop_returns_to_lower_body(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        self.assertEqual(b.focus, self.obj1)
        self.assertEqual(b.pop(), self.obj1)
        self.assertEqual(b.focus, self.char1)

    def test_pop_to_empty_is_ooc(self):
        b = self._binding()
        b.push(self.char1)
        self.assertEqual(b.pop(), self.char1)
        self.assertEqual(b.db_focus_stack, [])
        self.assertEqual(b.focus, self.account)
        # already at the floor — nothing left to pop.
        self.assertIsNone(b.pop())

    def test_collapse_unwinds_top_first_to_body(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        b.push(self.obj2)
        dropped = b.collapse_to(self.char1)
        self.assertEqual(dropped, [self.obj2, self.obj1])
        self.assertEqual(b.focus, self.char1)

    def test_collapse_to_floor_empties_stack(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        dropped = b.collapse_to_floor()
        self.assertEqual(dropped, [self.obj1, self.char1])
        self.assertEqual(b.db_focus_stack, [])
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

    # -- controller vs ownership --------------------------------------------
    def test_for_identity_reassigns_controller_not_ownership(self):
        # for_identity points the CONTROLLER (driver/floor) at the caller; it
        # must never change the identity's durable ownership.
        b = self._binding()  # controller self.account
        identity = b.db_identity
        owner_before = identity.db_account_id
        same = ControlBinding.for_identity(self.account2, identity)
        self.assertEqual(same.pk, b.pk)
        self.assertEqual(same.db_account_id, self.account2.id)  # controller moved
        self.assertEqual(identity.db_account_id, owner_before)  # ownership intact

    def test_for_identity_creates_empty_stack_cached(self):
        # A freshly created graph starts OOC and is the canonical idmapper
        # instance (so co-sessions never diverge from the creator).
        identity = create.create_object(self.character_typeclass, key="FreshChar")
        b = ControlBinding.for_identity(self.account, identity)
        self.assertEqual(b.db_focus_stack, [])
        self.assertIs(ControlBinding.objects.get(pk=b.pk), b)

    def test_ensure_playable_sets_ownership_only(self):
        # ensure_playable is ownership backfill; it sets ObjectDB.db_account and
        # does not mint a control graph.
        identity = create.create_object(self.character_typeclass, key="OwnMe")
        identity.db_account = None
        identity.save(update_fields=["db_account"])
        self.assertTrue(ControlBinding.ensure_playable(self.account, identity))
        self.assertEqual(identity.db_account_id, self.account.id)
        self.assertFalse(ControlBinding.objects.filter(db_identity=identity).exists())
        # idempotent
        self.assertFalse(ControlBinding.ensure_playable(self.account, identity))

    # -- idmapper sharing (the staleness fix) -------------------------------
    def test_idmapper_shares_instance_across_gets(self):
        b = self._binding()
        h1 = ControlBinding.objects.get(pk=b.pk)
        h2 = ControlBinding.objects.get(pk=b.pk)
        self.assertIs(h1, h2)

    def test_co_session_push_visible_without_refresh(self):
        # Two handles to the same row (as two sessions resolve it) are the one
        # shared instance, so a push by one is seen by the other immediately.
        b = self._binding()
        h1 = ControlBinding.objects.get(pk=b.pk)
        h2 = ControlBinding.objects.get(pk=b.pk)
        h1.push(self.char1)
        self.assertEqual(h2.focus, self.char1)
        self.assertEqual(h2.db_generation, h1.db_generation)

    def test_reload_reconstructs_stack_from_db(self):
        b = self._binding()
        b.push(self.char1)
        b.push(self.obj1)
        gen = b.db_generation
        # Drop the in-memory instance; the persisted row alone rebuilds it.
        ControlBinding.flush_instance_cache(force=True)
        fresh = ControlBinding.objects.get(pk=b.pk)
        self.assertEqual(fresh.focus, self.obj1)
        self.assertEqual(fresh.stack_objects, [self.char1, self.obj1])
        self.assertEqual(fresh.db_generation, gen)
