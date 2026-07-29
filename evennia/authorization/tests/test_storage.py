"""Persistent authorization storage and rollout tests."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from evennia.authorization import service as authorization_service
from evennia.authorization.legacy_import.migration import migrate_resource
from evennia.authorization.service import access_check, authorize
from evennia.authorization.storage import (
    clear_authorization_caches,
    delegate_grant,
    grant_capability,
    issue_recovery_grant,
    load_grants,
    load_policy,
    load_resource,
    preload_policy_packages,
    principal_is_suspended,
    principal_refs,
    set_principal_suspended,
    set_scope_labels,
)
from evennia.server.models import AuthorizationAuditEvent, AuthorizationPolicyOverride


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


class UnsafeRefResource(FakeResource):
    """Resource whose explicit reference is unsafe for Memcached keys."""

    def authorization_resource_ref(self):
        """Return a reference containing spaces and exceeding key limits."""

        return f"help:file:{'unsafe topic ' * 30}"


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

    def test_generation_cache_keys_are_backend_safe_and_bounded(self):
        resource = UnsafeRefResource()

        with patch("evennia.authorization.storage.cache.get", return_value=0) as cache_get:
            load_resource(resource)

        cache_key = cache_get.call_args.args[0]
        self.assertNotRegex(cache_key, r"[\x00-\x20\x7f]")
        self.assertLessEqual(len(cache_key), 250)

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

    def test_migration_drops_source_and_does_not_create_owner(self):
        resource = FakeResource(lock_storage="control:id(7);view:all()")
        result = migrate_resource(resource, freeze=True)

        self.assertFalse(result.frozen)
        self.assertEqual(result.grants_written, 1)
        self.assertFalse(hasattr(resource, "owner"))
        row = AuthorizationPolicyOverride.objects.get(
            resource_ref="object:42", access_type="control"
        )
        self.assertEqual(row.legacy_shadow, "")
        self.assertFalse(row.legacy_frozen)

    def test_access_facade_uses_live_structured_policy(self):
        principal = FakePrincipal()
        resource = FakeResource(lock_storage="view:none()")
        migrate_resource(resource, source="view:all()")
        allowed, decision = access_check(
            resource,
            principal,
            "view",
            default=False,
        )

        self.assertTrue(allowed)
        self.assertTrue(decision.allowed)

    def test_missing_policy_fails_closed_without_legacy_evaluation(self):
        principal = FakePrincipal()
        resource = FakeResource(lock_storage="view:all()")

        allowed, decision = access_check(resource, principal, "view", default=False)

        self.assertFalse(allowed)
        self.assertEqual(decision.reason_code, "no_structured_policy")

    def test_resource_policy_package_uses_one_query_for_multiple_operations(self):
        resource = FakeResource()
        migrate_resource(resource, source="view:all();edit:none()")
        clear_authorization_caches()

        with self.assertNumQueries(1):
            self.assertIsNotNone(load_policy(resource, "view"))
            self.assertIsNotNone(load_policy(resource, "edit"))
            self.assertIsNone(load_policy(resource, "missing"))

    def test_warm_structured_decision_performs_zero_sql(self):
        principal = FakePrincipal()
        resource = FakeResource()
        migrate_resource(resource, source="view:all()")
        clear_authorization_caches()

        allowed, _ = access_check(
            resource,
            principal,
            "view",
            default=False,
        )
        self.assertTrue(allowed)

        with self.assertNumQueries(0):
            allowed, _ = access_check(
                resource,
                principal,
                "view",
                default=False,
            )
        self.assertTrue(allowed)

    def test_policy_packages_batch_warm_in_one_query(self):
        first = FakeResource(pk=42)
        second = FakeResource(pk=43)
        migrate_resource(first, source="view:all()")
        migrate_resource(second, source="edit:none()")
        clear_authorization_caches()

        with self.assertNumQueries(1):
            self.assertEqual(preload_policy_packages((first, second)), 2)
        with self.assertNumQueries(0):
            self.assertIsNotNone(load_policy(first, "view"))
            self.assertIsNotNone(load_policy(second, "edit"))
