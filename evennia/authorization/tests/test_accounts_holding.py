"""``accounts_holding`` names the same accounts as ``world_capabilities``, read from rows."""

from datetime import timedelta

from django.utils import timezone

from evennia.accounts.models import AccountDB
from evennia.authorization.storage import (
    accounts_holding,
    create_principal_group,
    grant_capability,
    join_principal_group,
    revoke_grant,
    set_principal_suspended,
    world_capabilities,
)
from evennia.utils import create
from evennia.utils.test_resources import BaseEvenniaTest

CAPABILITY = "engine.world.announce"


class AccountsHoldingTest(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        self.already = self.by_account()
        self.account3 = create.create_account(
            "Third", email="third@test.com", password="testpassword"
        )
        self.char3 = create.create_object(
            self.character_typeclass, key="Char3", location=self.room1
        )
        self.char3.account = self.account3

    def by_account(self):
        return {
            account.id
            for account in AccountDB.objects.all()
            if CAPABILITY in world_capabilities(account)
        }

    def grant(self, principal_ref, **fields):
        fields.setdefault("scope_kind", "world")
        fields.setdefault("scope_key", "*")
        return grant_capability(principal_ref, CAPABILITY, **fields)

    def assertHolders(self, expected):
        """Both readings agree, and name ``expected`` beyond the fixture's own holders."""
        holders = accounts_holding(CAPABILITY)
        self.assertEqual(holders, frozenset(self.by_account()))
        self.assertEqual(holders - self.already, frozenset(expected))

    def test_no_grant_names_no_one(self):
        self.assertHolders(set())

    def test_a_grant_to_the_account(self):
        self.grant(f"account:{self.account2.id}")
        self.assertHolders({self.account2.id})

    def test_a_grant_to_the_account_entity(self):
        self.grant(f"entity:{self.account2.id}")
        self.assertHolders({self.account2.id})

    def test_a_grant_to_a_character_counts_for_its_owner(self):
        self.grant(f"object:{self.char3.id}")
        self.assertHolders({self.account3.id})

    def test_a_grant_to_a_character_with_no_owner_names_no_one(self):
        loose = create.create_object(self.character_typeclass, key="Loose", location=self.room1)
        self.grant(f"object:{loose.id}")
        self.assertHolders(set())

    def test_a_group_grant_counts_for_account_and_character_members(self):
        create_principal_group("crew")
        join_principal_group(f"account:{self.account2.id}", "crew")
        join_principal_group(f"object:{self.char3.id}", "crew")
        self.grant("group:crew")
        self.assertHolders({self.account2.id, self.account3.id})

    def test_a_revoked_or_expired_grant_does_not_count(self):
        grant = self.grant(f"account:{self.account3.id}")
        revoke_grant(grant.grant_id)
        self.grant(
            f"account:{self.account2.id}",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertHolders(set())

    def test_a_grant_below_world_scope_does_not_count(self):
        self.grant(f"account:{self.account3.id}", scope_kind="label", scope_key="area:docks")
        self.assertHolders(set())

    def test_a_constrained_grant_counts(self):
        self.grant(f"account:{self.account3.id}", constraints={"requires_label": "area:docks"})
        self.assertHolders({self.account3.id})

    def test_suspension_and_quell_do_not_matter(self):
        self.grant(f"account:{self.account3.id}")
        self.grant(f"account:{self.account2.id}")
        set_principal_suspended(f"account:{self.account3.id}", True, reason="test")
        self.account2.db._quell = True
        self.assertHolders({self.account2.id, self.account3.id})
