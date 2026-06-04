"""Tests for the Capability lattice + fresh scope-aware resolver (CM1 Phase 0f)."""

import unittest
from enum import IntFlag, auto
from types import SimpleNamespace

from evennia.actions.permission import (
    Capability,
    DefaultCapability,
    Scope,
    STAFF,
    get_capability_enum,
    rank_order,
    resolve_capabilities,
    capability_for_name,
    cumulative_rank_mask,
)

# Shorthands onto the concrete default lattice.
C = DefaultCapability


# --- lightweight stand-ins for game objects --------------------------------
class _Perms:
    """Mimics obj.permissions.all()."""

    def __init__(self, perms):
        self._perms = list(perms)

    def all(self):
        return list(self._perms)


class _Attrs:
    def __init__(self, **kw):
        self._kw = kw

    def get(self, key, default=None):
        return self._kw.get(key, default)


def _account(perms=(), quell=False):
    return SimpleNamespace(permissions=_Perms(perms), attributes=_Attrs(_quell=quell))


def _obj(perms=(), account=None):
    return SimpleNamespace(permissions=_Perms(perms), account=account)


class TestBaseIsExtensible(unittest.TestCase):
    def test_base_is_empty_member_less_intflag(self):
        # The base must stay member-less so it is still subclassable.
        self.assertTrue(issubclass(Capability, IntFlag))
        self.assertEqual(len(list(Capability)), 0)

    def test_default_subclasses_base(self):
        self.assertTrue(issubclass(DefaultCapability, Capability))
        self.assertIsInstance(DefaultCapability.BUILDER, Capability)

    def test_game_can_subclass_base(self):
        # Demonstrates the toolkit extension point: a game declares its own lattice.
        class GameCaps(Capability):
            NONE = 0
            MORTAL = auto()
            WIZARD = auto()

        self.assertIsInstance(GameCaps.WIZARD, Capability)


class TestDefaultCapability(unittest.TestCase):
    def test_membership_is_pure_int_no_db(self):
        mask = C.BUILDER | C.MODERATE
        self.assertIn(C.BUILDER, mask)
        self.assertIn(C.MODERATE, mask)
        self.assertNotIn(C.ADMIN, mask)

    def test_staff_mask(self):
        self.assertEqual(STAFF, C.BUILDER | C.ADMIN | C.DEVELOPER)

    def test_rank_order(self):
        ranks = rank_order(C)
        self.assertEqual(ranks[0], C.GUEST)
        self.assertEqual(ranks[-1], C.DEVELOPER)


class TestActiveEnum(unittest.TestCase):
    def test_default_when_setting_unset(self):
        # settings.CAPABILITY_ENUM defaults to None in the test settings.
        self.assertIs(get_capability_enum(), DefaultCapability)


class TestCapabilityForName(unittest.TestCase):
    def test_known_ranks(self):
        self.assertEqual(capability_for_name("Builder"), C.BUILDER)
        self.assertEqual(capability_for_name("developer"), C.DEVELOPER)

    def test_case_and_plural_insensitive(self):
        self.assertEqual(capability_for_name("BUILDERS"), C.BUILDER)
        self.assertEqual(capability_for_name("players"), C.PLAYER)

    def test_aliases(self):
        self.assertEqual(capability_for_name("Immortals"), C.DEVELOPER)
        self.assertEqual(capability_for_name("Wizard"), C.ADMIN)

    def test_cross_cutting(self):
        self.assertEqual(capability_for_name("Moderate"), C.MODERATE)

    def test_unknown_returns_none(self):
        self.assertIsNone(capability_for_name("Janitor"))
        self.assertIsNone(capability_for_name(""))
        self.assertIsNone(capability_for_name(None))


class TestCumulativeRankMask(unittest.TestCase):
    def test_cumulative(self):
        mask = cumulative_rank_mask(C.HELPER)
        self.assertEqual(mask, C.GUEST | C.PLAYER | C.HELPER)

    def test_non_rank_returns_empty(self):
        self.assertEqual(cumulative_rank_mask(C.MODERATE), C(0))


class TestResolveNoAccount(unittest.TestCase):
    """A bare object (no controlling account) uses its own permissions."""

    def test_hierarchy_encoded_at_resolve_time(self):
        mask = resolve_capabilities(_obj(["Builder"]))
        for lower in (C.GUEST, C.PLAYER, C.HELPER, C.BUILDER):
            self.assertIn(lower, mask)
        self.assertNotIn(C.ADMIN, mask)
        self.assertNotIn(C.DEVELOPER, mask)

    def test_admin_implies_builder(self):
        mask = resolve_capabilities(_obj(["Admin"]))
        self.assertIn(C.BUILDER, mask)
        self.assertIn(C.ADMIN, mask)
        self.assertNotIn(C.DEVELOPER, mask)

    def test_highest_rank_wins(self):
        mask = resolve_capabilities(_obj(["Player", "Developer", "Builder"]))
        self.assertIn(C.DEVELOPER, mask)
        self.assertIn(C.BUILDER, mask)

    def test_cross_cutting_independent_of_rank(self):
        mask = resolve_capabilities(_obj(["Player", "Moderate"]))
        self.assertIn(C.PLAYER, mask)
        self.assertIn(C.MODERATE, mask)
        self.assertNotIn(C.BUILDER, mask)

    def test_unknown_permissions_ignored(self):
        mask = resolve_capabilities(_obj(["Janitor", "Builder"]))
        self.assertIn(C.BUILDER, mask)

    def test_none_object(self):
        self.assertEqual(resolve_capabilities(None), C(0))

    def test_empty_perms(self):
        self.assertEqual(resolve_capabilities(_obj([])), C(0))


class TestResolveEffectiveWithAccount(unittest.TestCase):
    """EFFECTIVE scope mirrors perm(): unquelled uses account rank, quelled uses
    the lower of account/puppet, cross-cutting bits union."""

    def test_unquelled_uses_account_rank(self):
        acct = _account(["Developer"])
        obj = _obj(["Builder"], account=acct)
        mask = resolve_capabilities(obj)  # EFFECTIVE default
        self.assertIn(C.DEVELOPER, mask)  # account rank, not the puppet's Builder

    def test_quelled_uses_lower_of_pair(self):
        acct = _account(["Developer"], quell=True)
        obj = _obj(["Builder"], account=acct)
        mask = resolve_capabilities(obj)
        self.assertIn(C.BUILDER, mask)
        self.assertNotIn(C.ADMIN, mask)  # quelled down to the object's Builder
        self.assertNotIn(C.DEVELOPER, mask)

    def test_quelled_superuser_uses_puppet_rank(self):
        acct = SimpleNamespace(
            permissions=_Perms([]),
            attributes=_Attrs(_quell=True),
            is_superuser=True,
        )
        obj = _obj(["Player"], account=acct)
        mask = resolve_capabilities(obj)
        self.assertIn(C.PLAYER, mask)
        self.assertNotIn(C.BUILDER, mask)

    def test_quelled_staff_account_not_inherited_by_unranked_puppet(self):
        acct = _account(["Developer"], quell=True)
        obj = _obj([], account=acct)
        mask = resolve_capabilities(obj)
        self.assertNotIn(C.BUILDER, mask)
        self.assertNotIn(C.DEVELOPER, mask)

    def test_unquelled_superuser_has_top_rank(self):
        acct = SimpleNamespace(
            permissions=_Perms([]),
            attributes=_Attrs(_quell=False),
            is_superuser=True,
        )
        obj = _obj([], account=acct)
        mask = resolve_capabilities(obj)
        self.assertIn(C.DEVELOPER, mask)

    def test_cross_cutting_unions_account_and_puppet(self):
        acct = _account(["Player", "Moderate"])
        obj = _obj(["Impersonate"], account=acct)
        mask = resolve_capabilities(obj)
        self.assertIn(C.MODERATE, mask)
        self.assertIn(C.IMPERSONATE, mask)


class TestResolveScopes(unittest.TestCase):
    def test_account_scope_ignores_puppet(self):
        acct = _account(["Player"])
        obj = _obj(["Developer"], account=acct)
        mask = resolve_capabilities(obj, scope=Scope.ACCOUNT)
        self.assertIn(C.PLAYER, mask)
        self.assertNotIn(C.BUILDER, mask)

    def test_puppet_scope_ignores_account(self):
        acct = _account(["Developer"])
        obj = _obj(["Player"], account=acct)
        mask = resolve_capabilities(obj, scope=Scope.PUPPET)
        self.assertIn(C.PLAYER, mask)
        self.assertNotIn(C.BUILDER, mask)

    def test_account_scope_falls_back_to_obj_when_no_account(self):
        mask = resolve_capabilities(_obj(["Builder"]), scope=Scope.ACCOUNT)
        self.assertIn(C.BUILDER, mask)


if __name__ == "__main__":
    unittest.main()
