"""Groups, policy bundles, constraint registry, and predicate purity."""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings

from evennia.authorization.engine import (
    AuthorizationContext,
    GrantSnapshot,
    ResourceSnapshot,
    evaluate,
)
from evennia.authorization.policy import (
    Always,
    AuthorizationPurityError,
    Never,
    PredicateRequirement,
    register_grant_constraint,
    register_predicate_provider,
)
from evennia.authorization.service import has_capability
from evennia.authorization.storage import (
    activate_policy_bundle,
    clear_authorization_caches,
    compile_policy_bundle,
    create_principal_group,
    delete_principal_group,
    grant_capability,
    join_principal_group,
    leave_principal_group,
    load_grants,
    load_policy,
    principal_groups,
    set_scope_labels,
)
from evennia.authorization.tests.test_storage import FakePrincipal, FakeResource
from evennia.server.models import (
    AuthorizationAuditEvent,
    AuthorizationGrant,
    AuthorizationGroupMembership,
    AuthorizationPolicyBundle,
    AuthorizationPolicyOverride,
)

CAPABILITY = "engine.object.edit"


@override_settings(
    AUTHORIZATION_OFFLOOP_SNAPSHOTS=True,
    AUTHORIZATION_SHARED_INVALIDATION=False,
)
class AuthorizationModelTestBase(TestCase):
    """Shared cache hygiene for the model-hardening suites."""

    def setUp(self):
        clear_authorization_caches()

    def tearDown(self):
        clear_authorization_caches()
        super().tearDown()


class PrincipalGroupTest(AuthorizationModelTestBase):
    def test_group_membership_confers_group_grant_and_leaving_revokes(self):
        principal = FakePrincipal()
        create_principal_group("staff.chorus.magister", category="staff")
        join_principal_group("object:7", "staff.chorus.magister", provenance="test")
        grant_capability(
            "group:staff.chorus.magister",
            CAPABILITY,
            scope_kind="world",
            scope_key="*",
            provenance="test",
        )

        snapshot = load_grants(principal)
        self.assertIn(CAPABILITY, snapshot.by_capability)
        self.assertIn("group:staff.chorus.magister", snapshot.group_refs)
        self.assertEqual(principal_groups("object:7"), ("staff.chorus.magister",))

        leave_principal_group("object:7", "staff.chorus.magister", reason="test")

        self.assertNotIn(CAPABILITY, load_grants(principal).by_capability)

    def test_group_grant_edit_invalidates_members_without_fanout(self):
        principal = FakePrincipal()
        create_principal_group("staff.forge.adept")
        join_principal_group("object:7", "staff.forge.adept")
        grant_capability("group:staff.forge.adept", CAPABILITY, scope_kind="world", scope_key="*")
        self.assertIn(CAPABILITY, load_grants(principal).by_capability)

        grant_capability(
            "group:staff.forge.adept",
            "engine.authorization.break_glass",
            scope_kind="world",
            scope_key="*",
        )

        snapshot = load_grants(principal)
        self.assertIn("engine.authorization.break_glass", snapshot.by_capability)

    def test_unknown_group_grant_and_bad_refs_fail_closed(self):
        with self.assertRaises(ValueError):
            grant_capability("group:staff.missing", CAPABILITY, scope_kind="world", scope_key="*")
        with self.assertRaises(ValueError):
            create_principal_group("Bad Ref!")
        with self.assertRaises(ValueError):
            join_principal_group("object:7", "staff.missing")

    def test_delete_group_removes_memberships_grants_and_bumps_members(self):
        principal = FakePrincipal()
        create_principal_group("staff.logos.adept")
        join_principal_group("object:7", "staff.logos.adept")
        grant_capability("group:staff.logos.adept", CAPABILITY, scope_kind="world", scope_key="*")

        removed = delete_principal_group("staff.logos.adept", reason="test")

        self.assertEqual(removed, 1)
        self.assertFalse(
            AuthorizationGroupMembership.objects.filter(group_ref="staff.logos.adept").exists()
        )
        self.assertFalse(
            AuthorizationGrant.objects.filter(
                principal_ref="group:staff.logos.adept", revoked_at__isnull=True
            ).exists()
        )
        self.assertNotIn(CAPABILITY, load_grants(principal).by_capability)

    def test_group_mutations_are_audited(self):
        create_principal_group("staff.chorus.adept")
        join_principal_group("object:7", "staff.chorus.adept")
        leave_principal_group("object:7", "staff.chorus.adept")
        delete_principal_group("staff.chorus.adept")

        kinds = set(AuthorizationAuditEvent.objects.values_list("kind", flat=True))
        self.assertTrue({"group_created", "group_joined", "group_left", "group_deleted"} <= kinds)


class PolicyBundleTest(AuthorizationModelTestBase):
    def _write_override(self, policy):
        AuthorizationPolicyOverride.objects.update_or_create(
            resource_ref="object:42",
            access_type="view",
            defaults={"policy": policy.to_data()},
        )

    def test_bundle_is_opt_in_and_authoritative_once_active(self):
        resource = FakeResource()
        self._write_override(Always())
        clear_authorization_caches()
        self.assertIsInstance(load_policy(resource, "view"), Always)

        version = compile_policy_bundle(provenance="test")
        self.assertEqual(version, 1)
        self.assertTrue(AuthorizationPolicyBundle.objects.get(version=1).active)
        self.assertIsInstance(load_policy(resource, "view"), Always)

        # Live override edits no longer change evaluation while a bundle is active.
        self._write_override(Never())
        clear_authorization_caches()
        self.assertIsInstance(load_policy(resource, "view"), Always)

        second = compile_policy_bundle(provenance="test")
        self.assertIsInstance(load_policy(resource, "view"), Never)

        # Rollback is one activation.
        activate_policy_bundle(version, reason="rollback")
        self.assertIsInstance(load_policy(resource, "view"), Always)
        self.assertEqual(second, 2)

    def test_malformed_override_never_compiles_into_a_bundle(self):
        self._write_override(Always())
        AuthorizationPolicyOverride.objects.filter(resource_ref="object:42").update(
            policy={"schema": "auth.policy.v1", "type": "bogus"}
        )
        clear_authorization_caches()

        with self.assertRaises(ValueError):
            compile_policy_bundle(provenance="test")

        self.assertFalse(AuthorizationPolicyBundle.objects.exists())

    def test_activation_is_audited_and_generation_bumped(self):
        self._write_override(Always())
        compile_policy_bundle(provenance="test")
        activate_policy_bundle(1)

        kinds = set(AuthorizationAuditEvent.objects.values_list("kind", flat=True))
        self.assertTrue({"policy_bundle_compiled", "policy_bundle_activated"} <= kinds)


class GrantConstraintTest(AuthorizationModelTestBase):
    def setUp(self):
        super().setUp()
        self.principal = FakePrincipal()
        self.resource = FakeResource()

    def _grant(self, constraints):
        grant_capability(
            "object:7",
            CAPABILITY,
            scope_kind="world",
            scope_key="*",
            constraints=constraints,
        )

    def test_requires_label_constraint(self):
        self._grant({"requires_label": "area:market"})
        self.assertFalse(has_capability(self.principal, CAPABILITY, resource=self.resource))

        set_scope_labels(self.resource, {"area:market"})
        self.assertTrue(has_capability(self.principal, CAPABILITY, resource=self.resource))

    def test_online_constraint(self):
        self._grant({"online": True})
        self.assertFalse(has_capability(self.principal, CAPABILITY, resource=self.resource))

        grant_capability(
            "object:7",
            CAPABILITY,
            scope_kind="world",
            scope_key="*",
            constraints={"online": False},
        )
        self.assertTrue(has_capability(self.principal, CAPABILITY, resource=self.resource))

    def test_time_window_constraint(self):
        now = datetime.now()
        inside = now - timedelta(minutes=1)
        outside = now + timedelta(hours=2)
        self._grant({"time_window": f"{inside:%H:%M}-{(now + timedelta(minutes=1)):%H:%M}"})
        self.assertTrue(has_capability(self.principal, CAPABILITY, resource=self.resource))

        grant_capability(
            "object:7",
            CAPABILITY,
            scope_kind="world",
            scope_key="*",
            constraints={"time_window": f"{outside:%H:%M}-{(outside + timedelta(hours=1)):%H:%M}"},
        )
        self.assertFalse(has_capability(self.principal, CAPABILITY, resource=self.resource))

    def test_unknown_and_malformed_constraints_fail_closed(self):
        with self.assertRaises(ValueError):
            self._grant({"not_a_constraint": 1})
        with self.assertRaises(ValueError):
            self._grant({"online": "yes"})
        with self.assertRaises(ValueError):
            self._grant({"time_window": "noonish"})

    def test_conflicting_constraint_registration_is_rejected(self):
        register_grant_constraint("late", lambda *args: True)
        with self.assertRaises(ValueError):
            register_grant_constraint("late", lambda *args: False)


class PredicatePurityTest(AuthorizationModelTestBase):
    def _context(self):
        return AuthorizationContext(
            principal=FakePrincipal(), resource=FakeResource(), session=None
        )

    def _evaluate(self, policy):
        return evaluate(
            policy,
            GrantSnapshot("object:7", {}, 0),
            ResourceSnapshot("object:42", "object", frozenset(), 0),
            self._context(),
        )

    def test_impure_predicate_raises_in_error_mode(self):
        def impure(context, params):
            return AuthorizationGrant.objects.count() > 0

        register_predicate_provider("test.impure", impure)
        with override_settings(AUTHORIZATION_PREDICATE_PURITY="error"):
            with self.assertRaises(AuthorizationPurityError):
                self._evaluate(PredicateRequirement("test.impure"))

    def test_impure_predicate_logs_in_log_mode(self):
        def impure(context, params):
            return AuthorizationGrant.objects.count() >= 0

        register_predicate_provider("test.impure_log", impure)
        with override_settings(AUTHORIZATION_PREDICATE_PURITY="log"):
            with patch("evennia.utils.logger.log_warn") as warn:
                decision = self._evaluate(PredicateRequirement("test.impure_log"))
        self.assertTrue(decision.allowed)
        warn.assert_called_once()

    def test_pure_predicate_passes_in_error_mode(self):
        register_predicate_provider("test.pure", lambda context, params: True)
        with override_settings(AUTHORIZATION_PREDICATE_PURITY="error"):
            decision = self._evaluate(PredicateRequirement("test.pure"))
        self.assertTrue(decision.allowed)

    def test_async_predicate_registration_is_rejected(self):
        async def provider(context, params):
            return True

        with self.assertRaises(ValueError):
            register_predicate_provider("test.async", provider)
