"""The server.0013 and 0014 migrations keep every verb a broad-key holder could run."""

import importlib
from datetime import timedelta

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from evennia.authorization.service import has_capability
from evennia.authorization.storage import (
    clear_authorization_caches,
    create_principal_group,
    delegate_grant,
    grant_capability,
    join_principal_group,
    revoke_grant,
    world_capabilities,
)
from evennia.server.models import AuthorizationAuditEvent, AuthorizationGrant

migration = importlib.import_module("evennia.server.migrations.0014_split_engine_keys")
source_uniq = importlib.import_module("evennia.server.migrations.0013_grant_source_uniq")


def _active(principal_ref):
    return {
        (grant.capability, grant.scope_kind, grant.scope_key, grant.expires_at)
        for grant in AuthorizationGrant.objects.filter(
            principal_ref=principal_ref, revoked_at__isnull=True
        )
    }


class SplitEngineKeysTest(TestCase):
    def test_builder_gains_each_key_split_from_build(self):
        grant_capability("account:1", "engine.world.build", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        self.assertEqual(
            {cap for cap, *_ in _active("account:1")},
            {
                "engine.world.build",
                "engine.object.force",
                "engine.authorization.manage",
                "engine.world.announce",
                "engine.world.search",
            },
        )

    def test_moderator_gains_announce_and_password(self):
        grant_capability("account:2", "engine.moderation.manage", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        self.assertEqual(
            {cap for cap, *_ in _active("account:2")},
            {"engine.moderation.manage", "engine.world.announce", "engine.account.password"},
        )

    def test_new_grants_keep_the_scope_and_expiry_of_their_source(self):
        expires = timezone.now() + timedelta(days=3)
        grant_capability(
            "account:3",
            "engine.world.build",
            scope_kind="label",
            scope_key="area:docks",
            expires_at=expires,
        )
        migration.split_keys(apps, None)
        self.assertIn(("engine.object.force", "label", "area:docks", expires), _active("account:3"))

    def test_revoked_grants_give_nothing(self):
        grant = grant_capability(
            "account:4", "engine.world.build", scope_kind="world", scope_key="*"
        )
        revoke_grant(grant.grant_id, actor_ref="account:1", reason="test")
        migration.split_keys(apps, None)
        self.assertEqual(_active("account:4"), set())

    def test_running_twice_adds_nothing(self):
        grant_capability("account:5", "engine.world.build", scope_kind="world", scope_key="*")
        grant_capability("account:5", "engine.moderation.manage", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        count = AuthorizationGrant.objects.count()
        migration.split_keys(apps, None)
        self.assertEqual(AuthorizationGrant.objects.count(), count)

    def test_each_new_grant_is_audited(self):
        grant_capability("account:6", "engine.moderation.manage", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        self.assertEqual(
            set(
                AuthorizationAuditEvent.objects.filter(
                    principal_ref="account:6", actor_ref="system:migration"
                ).values_list("capability", flat=True)
            ),
            {"engine.world.announce", "engine.account.password"},
        )

    def test_reverse_removes_only_migration_grants(self):
        grant_capability("account:7", "engine.world.build", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        migration.unsplit_keys(apps, None)
        self.assertEqual({cap for cap, *_ in _active("account:7")}, {"engine.world.build"})

    def test_revoking_the_source_revokes_what_it_gave(self):
        grant = grant_capability(
            "account:8", "engine.world.build", scope_kind="world", scope_key="*"
        )
        migration.split_keys(apps, None)
        revoke_grant(grant.grant_id, actor_ref="account:1", reason="test")
        self.assertEqual(_active("account:8"), set())

    def test_a_delegated_source_gives_only_delegable_keys(self):
        grant = grant_capability(
            "account:9", "engine.world.build", scope_kind="world", scope_key="*"
        )
        delegate_grant(grant.grant_id, "account:10", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        self.assertEqual(
            {cap for cap, *_ in _active("account:10")},
            {"engine.world.build", "engine.world.search"},
        )

    def test_each_source_gives_its_own_row_on_its_own_terms(self):
        expires = timezone.now() + timedelta(days=1)
        build = grant_capability(
            "account:11",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            expires_at=expires,
        )
        moderation = grant_capability(
            "account:11", "engine.moderation.manage", scope_kind="world", scope_key="*"
        )
        migration.split_keys(apps, None)
        self.assertEqual(
            set(
                AuthorizationGrant.objects.filter(
                    principal_ref="account:11", capability="engine.world.announce"
                ).values_list("parent_grant_id", "expires_at")
            ),
            {(build.grant_id, expires), (moderation.grant_id, None)},
        )

    def test_a_key_two_sources_give_survives_revoking_one(self):
        build = grant_capability(
            "account:12", "engine.world.build", scope_kind="world", scope_key="*"
        )
        grant_capability(
            "account:12", "engine.moderation.manage", scope_kind="world", scope_key="*"
        )
        migration.split_keys(apps, None)
        revoke_grant(build.grant_id, actor_ref="account:1", reason="test")
        self.assertEqual(
            {cap for cap, *_ in _active("account:12")},
            {"engine.moderation.manage", "engine.world.announce", "engine.account.password"},
        )

    def test_reverse_removes_grants_delegated_from_migration_grants(self):
        grant_capability("account:13", "engine.world.build", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        search = AuthorizationGrant.objects.get(
            principal_ref="account:13", capability="engine.world.search"
        )
        delegate_grant(search.grant_id, "account:14", scope_kind="world", scope_key="*")
        migration.unsplit_keys(apps, None)
        self.assertEqual(_active("account:14"), set())

    def test_extending_a_source_extends_the_keys_split_from_it(self):
        grant_capability(
            "account:15",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            expires_at=timezone.now() + timedelta(days=1),
        )
        migration.split_keys(apps, None)
        grant_capability("account:15", "engine.world.build", scope_kind="world", scope_key="*")
        self.assertEqual({expiry for *_, expiry in _active("account:15")}, {None})

    def test_narrowing_a_source_narrows_the_keys_split_from_it(self):
        grant_capability("account:16", "engine.world.build", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        grant_capability(
            "account:16",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            constraints={"requires_label": "area:docks"},
        )
        rows = AuthorizationGrant.objects.filter(
            principal_ref="account:16", revoked_at__isnull=True
        )
        build = rows.get(capability="engine.world.build")
        self.assertTrue(build.constraints)
        self.assertEqual([row.constraints for row in rows], [build.constraints] * 5)

    def test_a_grant_delegated_to_someone_else_keeps_its_terms(self):
        build = grant_capability(
            "account:17", "engine.world.build", scope_kind="world", scope_key="*"
        )
        expires = timezone.now() + timedelta(days=2)
        delegate_grant(
            build.grant_id, "account:18", scope_kind="world", scope_key="*", expires_at=expires
        )
        grant_capability("account:17", "engine.world.build", scope_kind="world", scope_key="*")
        self.assertEqual({expiry for *_, expiry in _active("account:18")}, {expires})

    def test_a_key_two_sources_give_goes_with_both(self):
        build = grant_capability(
            "account:19", "engine.world.build", scope_kind="world", scope_key="*"
        )
        moderation = grant_capability(
            "account:19", "engine.moderation.manage", scope_kind="world", scope_key="*"
        )
        migration.split_keys(apps, None)
        revoke_grant(build.grant_id, actor_ref="account:1", reason="test")
        revoke_grant(moderation.grant_id, actor_ref="account:1", reason="test")
        self.assertEqual(_active("account:19"), set())

    def test_a_constrained_source_is_all_that_is_left_when_the_other_expires(self):
        grant_capability(
            "account:20",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            constraints={"requires_label": "area:docks"},
        )
        grant_capability(
            "account:20",
            "engine.moderation.manage",
            scope_kind="world",
            scope_key="*",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        migration.split_keys(apps, None)
        principal = _Principal(20)
        self.assertIn("engine.world.announce", world_capabilities(principal))
        self.assertNotIn("engine.world.announce", world_capabilities(principal, acting=True))

    def test_refreshing_one_source_leaves_the_other_sources_keys(self):
        expires = timezone.now() + timedelta(days=1)
        grant_capability(
            "account:21",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            expires_at=expires,
        )
        grant_capability(
            "account:21",
            "engine.moderation.manage",
            scope_kind="world",
            scope_key="*",
            expires_at=expires,
        )
        migration.split_keys(apps, None)
        longer = expires + timedelta(days=5)
        grant_capability(
            "account:21",
            "engine.moderation.manage",
            scope_kind="world",
            scope_key="*",
            expires_at=longer,
            constraints={"requires_label": "area:docks"},
        )
        terms = {
            (
                grant.capability,
                grant.parent_grant_id != "",
                grant.expires_at,
                bool(grant.constraints),
            )
            for grant in AuthorizationGrant.objects.filter(
                principal_ref="account:21", revoked_at__isnull=True
            )
            if grant.capability in ("engine.world.announce", "engine.account.password")
        }
        self.assertEqual(
            terms,
            {
                ("engine.world.announce", True, expires, False),
                ("engine.world.announce", True, longer, True),
                ("engine.account.password", True, longer, True),
            },
        )

    def test_an_expired_source_hides_its_keys_until_it_is_renewed(self):
        grant_capability(
            "account:22",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        migration.split_keys(apps, None)
        principal = _Principal(22)
        self.assertNotIn("engine.object.force", world_capabilities(principal))
        grant_capability("account:22", "engine.world.build", scope_kind="world", scope_key="*")
        self.assertIn("engine.object.force", world_capabilities(principal))

    def test_a_direct_grant_of_a_split_key_outlives_the_source(self):
        build = grant_capability(
            "account:23", "engine.world.build", scope_kind="world", scope_key="*"
        )
        grant_capability("account:23", "engine.object.force", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        revoke_grant(build.grant_id, actor_ref="account:1", reason="test")
        self.assertEqual(_active("account:23"), {("engine.object.force", "world", "*", None)})

    def test_a_split_key_outlives_a_direct_grant_of_it(self):
        grant_capability("account:30", "engine.world.build", scope_kind="world", scope_key="*")
        direct = grant_capability(
            "account:30", "engine.object.force", scope_kind="world", scope_key="*"
        )
        migration.split_keys(apps, None)
        revoke_grant(direct.grant_id, actor_ref="account:1", reason="test")
        self.assertIn(("engine.object.force", "world", "*", None), _active("account:30"))

    def test_revoking_the_root_reaches_keys_delegated_two_levels_down(self):
        build = grant_capability(
            "account:24", "engine.world.build", scope_kind="world", scope_key="*"
        )
        middle = delegate_grant(build.grant_id, "account:25", scope_kind="world", scope_key="*")
        migration.split_keys(apps, None)
        search = AuthorizationGrant.objects.get(
            parent_grant_id=middle.grant_id, capability="engine.world.search"
        )
        delegate_grant(search.grant_id, "account:26", scope_kind="world", scope_key="*")
        revoke_grant(build.grant_id, actor_ref="account:1", reason="test")
        self.assertEqual(_active("account:25") | _active("account:26"), set())

    def test_a_group_holding_build_gives_its_members_force_until_revoked(self):
        from django.contrib.auth import get_user_model

        account = get_user_model().objects.create_user(
            username="split-group", email="", password="q" * 16
        )
        create_principal_group("split-builders")
        join_principal_group(f"account:{account.pk}", "split-builders")
        build = grant_capability(
            "group:split-builders", "engine.world.build", scope_kind="world", scope_key="*"
        )
        migration.split_keys(apps, None)
        clear_authorization_caches()
        self.assertTrue(has_capability(account, "engine.object.force"))
        revoke_grant(build.grant_id, actor_ref="account:1", reason="test")
        self.assertFalse(has_capability(account, "engine.object.force"))

    def test_reversing_the_constraint_keeps_one_row_per_slot(self):
        build = grant_capability(
            "account:27", "engine.world.build", scope_kind="world", scope_key="*"
        )
        other = grant_capability(
            "account:28", "engine.world.build", scope_kind="world", scope_key="*"
        )
        direct = grant_capability(
            "account:29", "engine.world.build", scope_kind="world", scope_key="*"
        )
        delegate_grant(build.grant_id, "account:29", scope_kind="world", scope_key="*")
        delegate_grant(other.grant_id, "account:29", scope_kind="world", scope_key="*")
        source_uniq.collapse_duplicate_slots(apps, None)
        self.assertEqual(
            list(
                AuthorizationGrant.objects.filter(
                    principal_ref="account:29", revoked_at__isnull=True
                ).values_list("grant_id", flat=True)
            ),
            [direct.grant_id],
        )


class _Principal:
    """An account-shaped principal for the world_capabilities reads."""

    __module__ = "evennia.accounts.accounts"

    def __init__(self, pk):
        self.pk = pk
