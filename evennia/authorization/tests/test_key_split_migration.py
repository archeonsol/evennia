"""The server.0013 migration keeps every verb a broad-key holder could run."""

import importlib
from datetime import timedelta

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from evennia.authorization.storage import delegate_grant, grant_capability, revoke_grant
from evennia.server.models import AuthorizationAuditEvent, AuthorizationGrant

migration = importlib.import_module("evennia.server.migrations.0013_split_engine_keys")


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

    def test_a_permanent_source_beats_an_expiring_one(self):
        grant_capability(
            "account:11",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
            expires_at=timezone.now() + timedelta(days=1),
        )
        grant_capability(
            "account:11", "engine.moderation.manage", scope_kind="world", scope_key="*"
        )
        migration.split_keys(apps, None)
        self.assertIn(("engine.world.announce", "world", "*", None), _active("account:11"))

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
