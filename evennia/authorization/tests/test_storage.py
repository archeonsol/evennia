"""Persistent authorization storage and rollout tests."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from evennia.authorization.migration import migrate_resource
from evennia.authorization.service import access_check, authorize
from evennia.authorization.storage import (
    clear_authorization_caches,
    delegate_grant,
    grant_capability,
    issue_recovery_grant,
    load_grants,
    load_resource,
    principal_is_suspended,
    principal_refs,
    set_principal_suspended,
    set_scope_labels,
)
from evennia.locks.lockhandler import LockHandler
from evennia.server.models import (
    AuthorizationAuditEvent,
    AuthorizationPolicyOverride,
)


class FakePermissions:
    """Minimal legacy-permission handler."""

    def all(self):
        """Return no compatibility permissions."""

        return []


class FakeTags:
    """Minimal resource tag handler."""

    def all(self, **kwargs):
        """Return no direct tags."""

        return []


class FakePrincipal:
    """Stable object principal for grant loading."""

    __module__ = "game.objects"

    def __init__(self, pk=7):
        """Initialize the fake principal."""

        self.pk = pk
        self.permissions = FakePermissions()
        self.account = None


class FakeResource:
    """Stable object resource for policy migration."""

    __module__ = "game.objects"

    def __init__(self, pk=42, lock_storage="view:all()"):
        """Initialize the fake resource."""

        self.pk = pk
        self.tags = FakeTags()
        self.lock_storage = lock_storage
        self.locks = MagicMock()


class AuthorizationStorageTest(TestCase):
    """Persistent grants and materialized labels remain independently cached."""

    def tearDown(self):
        """Clear process caches between tests."""

        clear_authorization_caches()
        super().tearDown()

    def test_positive_grant_matches_authored_scope_label(self):
        principal = FakePrincipal()
        resource = FakeResource()
        set_scope_labels(resource, {"project:market"})
        grant_capability(
            "object:7",
            "engine.object.edit",
            scope_kind="label",
            scope_key="project:market",
        )
        AuthorizationPolicyOverride.objects.create(
            resource_ref="object:42",
            access_type="edit",
            policy={
                "schema": "auth.policy.v1",
                "type": "capability",
                "capability": "engine.object.edit",
            },
        )

        decision = authorize(principal, resource, "edit")

        self.assertTrue(decision.allowed)
        self.assertIn("project:market", load_resource(resource).labels)
        self.assertIn("engine.object.edit", load_grants(principal).by_capability)

    def test_live_driver_not_durable_owner_supplies_account_principal(self):
        driver = type("Account", (), {"pk": 9})()

        class DrivenPrincipal(FakePrincipal):
            @property
            def puppeteer(self):
                return driver

        principal = DrivenPrincipal()
        principal.account = type("Owner", (), {"pk": 99})()
        self.assertIn("account:9", principal_refs(principal))
        self.assertNotIn("account:99", principal_refs(principal))

    def test_recovery_grant_is_temporary_and_audited(self):
        grant = issue_recovery_grant(9, reason="restore authorization", ttl_seconds=120)
        self.assertEqual(grant.principal_ref, "account:9")
        self.assertIsNotNone(grant.expires_at)
        self.assertTrue(
            AuthorizationAuditEvent.objects.filter(
                principal_ref="account:9", capability="engine.authorization.break_glass"
            ).exists()
        )

    def test_delegation_cannot_widen_parent_scope(self):
        parent = grant_capability(
            "object:7",
            "engine.object.edit",
            scope_kind="label",
            scope_key="project:market",
        )
        with self.assertRaises(ValueError):
            delegate_grant(
                parent.grant_id,
                "object:8",
                scope_kind="world",
                scope_key="*",
            )

    def test_cached_temporary_grant_expires_without_mutation(self):
        principal = FakePrincipal()
        now = timezone.now()
        grant_capability(
            "object:7",
            "engine.object.edit",
            scope_kind="world",
            scope_key="*",
            expires_at=now + timedelta(seconds=60),
        )
        self.assertIn("engine.object.edit", load_grants(principal).by_capability)
        with patch(
            "evennia.authorization.storage.timezone.now",
            return_value=now + timedelta(seconds=61),
        ):
            self.assertNotIn("engine.object.edit", load_grants(principal).by_capability)

    def test_cached_suspension_expires_without_mutation(self):
        principal = FakePrincipal()
        now = timezone.now()
        set_principal_suspended(
            "object:7",
            True,
            reason="temporary",
            until=now + timedelta(seconds=60),
        )
        self.assertTrue(principal_is_suspended(principal))
        with patch(
            "evennia.authorization.storage.timezone.now",
            return_value=now + timedelta(seconds=61),
        ):
            self.assertFalse(principal_is_suspended(principal))

    def test_migration_retains_source_and_does_not_create_owner(self):
        resource = FakeResource(lock_storage="control:id(7);view:all()")
        result = migrate_resource(resource, freeze=True)

        self.assertTrue(result.frozen)
        self.assertEqual(result.grants_written, 1)
        self.assertFalse(hasattr(resource, "owner"))
        row = AuthorizationPolicyOverride.objects.get(
            resource_ref="object:42", access_type="control"
        )
        self.assertEqual(row.legacy_shadow, resource.lock_storage)
        self.assertTrue(row.legacy_frozen)

    @override_settings(AUTHORIZATION_RESOURCE_POLICIES={"object": "live"})
    def test_access_facade_uses_live_structured_policy(self):
        principal = FakePrincipal()
        resource = FakeResource(lock_storage="view:none()")
        migrate_resource(resource, source="view:all()")
        legacy = MagicMock(return_value=False)

        allowed, decision = access_check(
            resource,
            principal,
            "view",
            default=False,
            legacy_evaluator=legacy,
        )

        self.assertTrue(allowed)
        self.assertTrue(decision.allowed)
        legacy.assert_not_called()

    @override_settings(AUTHORIZATION_FROZEN_RESOURCE_KINDS=("object",))
    def test_frozen_lock_write_updates_policy_not_legacy_field(self):
        resource = FakeResource(lock_storage="view:all()")
        handler = LockHandler(resource)

        self.assertTrue(handler.add("edit:perm(Builder)"))

        self.assertEqual(resource.lock_storage, "view:all()")
        self.assertTrue(
            AuthorizationPolicyOverride.objects.filter(
                resource_ref="object:42", access_type="edit", legacy_frozen=True
            ).exists()
        )
