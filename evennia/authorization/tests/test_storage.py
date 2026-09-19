"""Persistent authorization storage and rollout tests."""

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from evennia.authorization import invalidation
from evennia.authorization import service as authorization_service
from evennia.authorization.legacy_import.migration import migrate_resource
from evennia.authorization.service import access_check, authorize
from evennia.authorization.storage import (
    AuthorizationSnapshotUnavailable,
    _fetch_authorization_snapshots,
    _install_authorization_snapshots,
    _prewarm_request,
    _suppression_spec,
    _worker_suppression_map,
    authorization_snapshot_scope,
    clear_authorization_caches,
    delegate_grant,
    ensure_authorization,
    grant_capabilities,
    grant_capability,
    issue_recovery_grant,
    load_grants,
    load_policy,
    load_resource,
    preload_policy_packages,
    prewarm_authorization,
    principal_is_suspended,
    principal_refs,
    set_principal_suspended,
    set_scope_labels,
)
from evennia.server.models import (
    AuthorizationAuditEvent,
    AuthorizationGrant,
    AuthorizationPolicyOverride,
)
from evennia.utils import clock, defer


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


class FakePuppet(FakePrincipal):
    """Fake puppeted body whose authority source is a stored account row."""

    __module__ = "game.objects"

    def __init__(self, pk, account):
        """Attach the account that owns the body."""

        super().__init__(pk)
        self.account = account


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


@override_settings(
    AUTHORIZATION_OFFLOOP_SNAPSHOTS=True,
    AUTHORIZATION_SHARED_INVALIDATION=False,
)
class AuthorizationPrewarmTest(TransactionTestCase):
    """Managed action scopes consume only owner-thread snapshots."""

    def tearDown(self):
        clear_authorization_caches()
        super().tearDown()

    def test_prewarm_loads_in_worker_and_snapshot_reads_issue_no_sql(self):
        principal = FakePrincipal()
        resource = FakeResource()
        grant_capability(
            "object:7",
            "engine.object.view",
            scope_kind="world",
            scope_key="*",
        )
        AuthorizationPolicyOverride.objects.create(
            resource_ref="object:42",
            access_type="view",
            policy={"schema": "auth.policy.v1", "type": "always"},
        )
        clear_authorization_caches()

        async def run():
            with (
                patch.object(clock, "loop_running", return_value=True),
                patch.object(defer, "in_thread", wraps=defer.in_thread) as offload,
            ):
                self.offload = offload
                return await prewarm_authorization((principal,), (resource,))

        self.assertTrue(asyncio.run(run()))
        self.assertEqual(self.offload.call_count, 1)
        with authorization_snapshot_scope(), self.assertNumQueries(0):
            self.assertIn("engine.object.view", load_grants(principal).by_capability)
            self.assertIn("object:42", load_resource(resource).labels)
            self.assertIsNotNone(load_policy(resource, "view"))
            self.assertFalse(principal_is_suspended(principal))

    def test_concurrent_cold_callers_coalesce_into_one_batch(self):
        principals = (FakePrincipal(7), FakePrincipal(8))
        resources = (FakeResource(42), FakeResource(43))
        requests = []

        def fake_fetch(request):
            requests.append(request)
            return {
                "generation_updates": [],
                "principals": [
                    {
                        "cache_key": spec["cache_key"],
                        "generation": max(spec["local_generations"].values(), default=0),
                        "grants_stale": True,
                        "grants": [],
                        "suppressed": False,
                        "suspension_stale": True,
                        "suspended": False,
                        "suspended_until": None,
                    }
                    for spec in request["principals"]
                ],
                "resources": [
                    {
                        "ref": spec["ref"],
                        "generation": spec["local_generation"],
                        "resource_stale": True,
                        "base_labels": spec["base_labels"],
                        "labels": [],
                        "policy_stale": True,
                        "policies": [],
                    }
                    for spec in request["resources"]
                ],
            }

        async def run():
            return await asyncio.gather(
                prewarm_authorization((principals[0],), (resources[0],)),
                prewarm_authorization((principals[1],), (resources[1],)),
            )

        with patch(
            "evennia.authorization.storage._fetch_authorization_snapshots",
            side_effect=fake_fetch,
        ):
            self.assertEqual(asyncio.run(run()), [True, True])
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(requests[0]["principals"]), 2)
        self.assertEqual(len(requests[0]["resources"]), 2)

    def test_prewarm_metrics_distinguish_wait_from_ready_cache(self):
        principal = FakePrincipal()
        resource = FakeResource()
        clear_authorization_caches()

        with patch("evennia.server.prometheus_metrics.record_authorization_prewarm_wait") as record:
            self.assertTrue(asyncio.run(prewarm_authorization((principal,), (resource,))))
            self.assertEqual(record.call_args.args[0], "waited")
            record.reset_mock()
            self.assertTrue(asyncio.run(prewarm_authorization((principal,), (resource,))))
            self.assertEqual(record.call_args.args[0], "ready")

    def test_snapshot_miss_falls_back_to_an_inline_read(self):
        """A coverage gap degrades to indexed reads instead of an error.

        The inline fallback is one membership lookup plus one grants query;
        group membership is loaded with the principal snapshot.
        """

        principal = FakePrincipal()
        with override_settings(AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR=False):
            clear_authorization_caches()
            with authorization_snapshot_scope(), self.assertNumQueries(2):
                snapshot = load_grants(principal)
        self.assertEqual(dict(snapshot.by_capability), {})

    def test_snapshot_miss_can_fail_loudly_for_ci(self):
        """The strict tripwire still refuses an uncovered evaluation."""

        principal = FakePrincipal()
        with override_settings(AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR=True):
            clear_authorization_caches()
            with authorization_snapshot_scope():
                with self.assertRaises(AuthorizationSnapshotUnavailable):
                    load_grants(principal)

    def test_snapshot_scope_is_local_to_the_task_that_entered_it(self):
        """Context copies into detached tasks must not inherit the scope."""

        principal = FakePrincipal()

        async def run():
            with authorization_snapshot_scope():
                with self.assertRaises(AuthorizationSnapshotUnavailable):
                    load_grants(principal)

                async def child():
                    return load_grants(principal)

                return await asyncio.create_task(child())

        with override_settings(AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR=True):
            clear_authorization_caches()
            snapshot = asyncio.run(run())
        self.assertEqual(dict(snapshot.by_capability), {})

    def test_prewarm_retry_is_bounded(self):
        """A snapshot that can never become ready must not spin forever."""

        principal = FakePrincipal()
        calls = []

        def empty_fetch(request):
            calls.append(request)
            return {"generation_updates": [], "principals": [], "resources": []}

        async def run():
            return await prewarm_authorization((principal,), ())

        with patch(
            "evennia.authorization.storage._fetch_authorization_snapshots",
            side_effect=empty_fetch,
        ):
            self.assertFalse(asyncio.run(run()))
        self.assertEqual(len(calls), 3)

    def test_third_refresh_can_satisfy_the_request(self):
        """Readiness is checked after the final bounded refresh."""

        principal = FakePrincipal()
        resource = FakeResource()
        calls = []
        fetch = _fetch_authorization_snapshots

        def staged_fetch(request):
            calls.append(request)
            if len(calls) < 3:
                return {"generation_updates": [], "principals": [], "resources": []}
            return fetch(request)

        with patch(
            "evennia.authorization.storage._fetch_authorization_snapshots",
            side_effect=staged_fetch,
        ):
            self.assertTrue(asyncio.run(prewarm_authorization((principal,), (resource,))))
        self.assertEqual(len(calls), 3)

    def test_install_never_lowers_a_shared_generation(self):
        """Out-of-order batches cannot roll the generation pointer backwards."""

        from evennia.authorization import storage as storage_module

        cache_key = "evennia:authgen:resource:out-of-order"
        storage_module._shared_generation_cache[cache_key] = (0.0, 5)
        _install_authorization_snapshots(
            {
                "generation_updates": [(cache_key, 3)],
                "principals": [],
                "resources": [],
            }
        )
        self.assertEqual(storage_module._shared_generation_cache[cache_key][1], 5)

    @override_settings(AUTHORIZATION_SHARED_INVALIDATION=True)
    def test_deferred_publish_never_lowers_cached_generation(self):
        """The deferred path may not drop a higher cached generation."""

        from evennia.authorization import storage as storage_module
        from evennia.authorization.storage import _generation_cache_key, _publish_generation

        cache_key = _generation_cache_key("resource", "deferred-max")
        storage_module._shared_generation_cache[cache_key] = (0.0, 5)
        with (
            patch.object(clock, "loop_running", return_value=True),
            patch.object(clock, "is_io_owner", return_value=True),
            patch.object(defer, "background") as background,
        ):
            value = _publish_generation("resource", "deferred-max", 3)
        self.assertEqual(value, 3)
        self.assertEqual(storage_module._shared_generation_cache[cache_key][1], 5)
        background.assert_called_once()

    def test_broken_queued_item_does_not_kill_the_shared_prewarm(self):
        """A typeclass gone at build time is dropped, not raised through waiters."""

        class Broken:
            """Stand-in for an object whose typeclass cannot load."""

            def __getattr__(self, name):
                raise RuntimeError("typeclass gone")

        calls = []

        def fetch(request):
            calls.append(request)
            return {"generation_updates": [], "principals": [], "resources": []}

        async def run():
            return await prewarm_authorization((FakePrincipal(pk=9101), Broken()), ())

        with patch(
            "evennia.authorization.storage._fetch_authorization_snapshots",
            side_effect=fetch,
        ):
            result = asyncio.run(run())

        self.assertIs(result, False)
        self.assertTrue(calls)
        for request in calls:
            self.assertEqual(
                [spec["cache_key"] for spec in request["principals"]],
                ["object:9101|entity:9101"],
            )

    def test_prewarm_coalescing_state_is_per_event_loop(self):
        """A waiter on one loop must never await a task bound to another."""

        import threading

        b_entered = threading.Event()
        a_done = threading.Event()
        b_done = threading.Event()
        results = {}

        def fetch(request):
            if any("9102" in spec["cache_key"] for spec in request["principals"]):
                b_entered.set()
                a_done.wait(5)
            else:
                b_entered.wait(5)
            return {"generation_updates": [], "principals": [], "resources": []}

        async def runner(pk):
            return await prewarm_authorization((FakePrincipal(pk=pk),), ())

        def worker(tag, pk, done_event):
            try:
                results[tag] = asyncio.run(runner(pk))
            except BaseException as exc:
                results[tag] = exc
            done_event.set()

        with patch(
            "evennia.authorization.storage._fetch_authorization_snapshots",
            side_effect=fetch,
        ):
            thread_a = threading.Thread(target=worker, args=("a", 9101, a_done), daemon=True)
            thread_b = threading.Thread(target=worker, args=("b", 9102, b_done), daemon=True)
            thread_a.start()
            thread_b.start()
            thread_a.join(15)
            thread_b.join(15)

        self.assertEqual(sorted(results), ["a", "b"])
        for outcome in results.values():
            self.assertIsInstance(outcome, bool)

    def test_ensure_authorization_coalesces_through_per_loop_state(self):
        """The push cold-fact path must coalesce like prewarm, on loop-owned state."""

        def fetch(request):
            return {"generation_updates": [], "principals": [], "resources": []}

        async def run():
            return await ensure_authorization((FakePrincipal(pk=9103),), ())

        with (
            patch(
                "evennia.authorization.storage._fetch_authorization_snapshots",
                side_effect=fetch,
            ),
            patch.object(invalidation, "push_enabled", return_value=True),
            patch.object(invalidation, "start"),
        ):
            result = asyncio.run(run())

        self.assertIsInstance(result, bool)

    def test_broken_queued_resource_is_dropped_and_flips_not_ready(self):
        """A dropped resource spec may not leave the request reported ready."""

        class Broken:
            """Stand-in for an object whose typeclass cannot load."""

            def __getattr__(self, name):
                raise RuntimeError("typeclass gone")

        resource = FakeResource()
        load_resource(resource)
        load_policy(resource, "view")

        ready, request = _prewarm_request((), (resource, Broken()), include_labels=False)

        self.assertFalse(ready)
        self.assertEqual([spec["ref"] for spec in request["resources"]], ["object:42"])

    def test_suppression_spec_reads_unflushed_handler_state(self):
        """A quell set moments ago must suppress before the next flush."""

        from django.contrib.auth import get_user_model

        account = get_user_model().objects.create_user(
            username="quell-live-doc", email="", password="q" * 16
        )
        account.attributes.add("_quell", True)

        spec = _suppression_spec(FakePuppet(7, account))
        self.assertTrue(spec["known"])
        self.assertTrue(spec["suppressed"])

    def test_worker_resolves_suppression_from_the_stored_row(self):
        """When the handler is unavailable, the worker reads the row itself."""

        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        account = user_model.objects.create_user(
            username="quell-stored-doc", email="", password="q" * 16
        )
        user_model.objects.filter(pk=account.pk).update(db_attrs={"~": {"_d": {"_quell": True}}})

        specs = [
            {
                "cache_key": "cold",
                "suppression": {
                    "known": False,
                    "suppressed": False,
                    "label": "accounts.accountdb",
                    "pk": account.pk,
                },
            }
        ]
        self.assertEqual(_worker_suppression_map(specs), {"cold": True})

    def test_prewarm_suppresses_grants_for_a_live_quell(self):
        """The end-to-end prewarm honors an unflushed quell like the inline read."""

        from django.contrib.auth import get_user_model

        account = get_user_model().objects.create_user(
            username="quell-prewarm", email="", password="q" * 16
        )
        account.attributes.add("_quell", True)
        principal = FakePuppet(7, account)
        grant_capability(
            f"account:{account.pk}",
            "engine.object.view",
            scope_kind="world",
            scope_key="*",
        )
        clear_authorization_caches()

        ready, request = _prewarm_request((principal,), (), include_labels=True)
        self.assertFalse(ready)
        self.assertTrue(request["principals"][0]["suppression"]["suppressed"])

        result = _fetch_authorization_snapshots(request)
        _install_authorization_snapshots(result)
        with authorization_snapshot_scope(), self.assertNumQueries(0):
            snapshot = load_grants(principal)
        self.assertNotIn("engine.object.view", snapshot.by_capability)


class BulkGrantTest(TestCase):
    """`grant_capabilities` is `grant_capability` in one transaction."""

    capabilities = ("engine.object.view", "engine.object.edit", "engine.object.move")

    def tearDown(self):
        """Clear process caches between tests."""

        clear_authorization_caches()
        super().tearDown()

    def _rows(self, principal_ref="account:1"):
        return {
            grant.capability: grant
            for grant in AuthorizationGrant.objects.filter(
                principal_ref=principal_ref, revoked_at__isnull=True
            )
        }

    def test_bulk_grant_matches_the_single_grant_path(self):
        grant_capabilities(
            "account:1",
            self.capabilities,
            scope_kind="world",
            scope_key="*",
            provenance="bulk",
        )
        for capability in self.capabilities:
            grant_capability(
                "account:2",
                capability,
                scope_kind="world",
                scope_key="*",
                provenance="bulk",
            )

        bulk = self._rows("account:1")
        singly = self._rows("account:2")
        self.assertEqual(set(bulk), set(self.capabilities))
        self.assertEqual(set(bulk), set(singly))
        for capability in self.capabilities:
            self.assertEqual(bulk[capability].scope_kind, singly[capability].scope_kind)
            self.assertEqual(bulk[capability].scope_key, singly[capability].scope_key)
            self.assertEqual(bulk[capability].provenance, singly[capability].provenance)
            self.assertEqual(bulk[capability].constraints, singly[capability].constraints)
            self.assertIsNotNone(bulk[capability].pk)
            self.assertIsNotNone(bulk[capability].created_at)
        self.assertEqual(
            AuthorizationAuditEvent.objects.filter(
                principal_ref="account:1", kind="grant_created"
            ).count(),
            len(self.capabilities),
        )

    def test_regranting_refreshes_rather_than_duplicates(self):
        grant_capabilities(
            "account:1",
            self.capabilities,
            scope_kind="world",
            scope_key="*",
            provenance="first",
        )
        before = self._rows()
        expires = timezone.now() + timedelta(days=1)

        grant_capabilities(
            "account:1",
            self.capabilities,
            scope_kind="world",
            scope_key="*",
            provenance="second",
            expires_at=expires,
        )

        after = self._rows()
        self.assertEqual(set(after), set(self.capabilities))
        for capability in self.capabilities:
            self.assertEqual(after[capability].pk, before[capability].pk)
            self.assertEqual(after[capability].provenance, "second")
            self.assertEqual(after[capability].expires_at, expires)
            # bulk_update does not run pre_save, so auto_now only advances
            # because the caller sets it. A stale updated_at would hide the
            # refresh from anything that reads grants by recency.
            self.assertGreater(after[capability].updated_at, before[capability].updated_at)

    def test_duplicate_capabilities_collapse_to_one_grant(self):
        returned = grant_capabilities(
            "account:1",
            ["engine.object.view", "engine.object.view", "engine.object.edit"],
            scope_kind="world",
            scope_key="*",
        )

        self.assertEqual(
            [grant.capability for grant in returned],
            ["engine.object.view", "engine.object.edit"],
        )
        self.assertEqual(len(self._rows()), 2)

    def test_unknown_capability_writes_nothing(self):
        with self.assertRaises(ValueError):
            grant_capabilities(
                "account:1",
                ["engine.object.view", "engine.object.not_a_capability"],
                scope_kind="world",
                scope_key="*",
            )

        self.assertEqual(self._rows(), {})
        self.assertFalse(AuthorizationAuditEvent.objects.filter(principal_ref="account:1").exists())

    def test_unsupported_constraints_are_rejected(self):
        with self.assertRaises(ValueError):
            grant_capabilities(
                "account:1",
                ["engine.object.view"],
                scope_kind="world",
                scope_key="*",
                constraints={"not_a_constraint": 1},
            )

        self.assertEqual(self._rows(), {})

    def test_no_capabilities_is_a_no_op(self):
        self.assertEqual(grant_capabilities("account:1", [], scope_kind="world", scope_key="*"), [])
        self.assertEqual(self._rows(), {})


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

        # The cold package load probes the active policy bundle (opt-in) and
        # reads override rows once; every later operation is local.
        with self.assertNumQueries(2):
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

        # One bundle-version probe plus one batched override query.
        with self.assertNumQueries(2):
            self.assertEqual(preload_policy_packages((first, second)), 2)
        with self.assertNumQueries(0):
            self.assertIsNotNone(load_policy(first, "view"))
            self.assertIsNotNone(load_policy(second, "edit"))


class MoveInvalidationTest(TestCase):
    """Move-time invalidation is content-gated and fails safe."""

    def test_stable_adapter_skips_invalidation(self):
        from evennia.authorization import storage
        from evennia.authorization.resources import ResourceAdapter

        adapter = ResourceAdapter("stable", lambda resource: True, lambda resource: "stable")
        with (
            patch.object(storage.resource_adapters, "for_resource", return_value=adapter),
            patch.object(storage, "bump_resource_generation") as bump,
            patch(
                "evennia.server.prometheus_metrics.record_authorization_move_invalidation"
            ) as record,
        ):
            self.assertFalse(storage.bump_resource_generation_after_move(FakeResource()))
        bump.assert_not_called()
        record.assert_called_once_with("skipped")

    def test_location_sensitive_adapter_bumps(self):
        from evennia.authorization import storage
        from evennia.authorization.resources import ResourceAdapter

        adapter = ResourceAdapter(
            "moving",
            lambda resource: True,
            lambda resource: "moving",
            location_sensitive=True,
        )
        resource = FakeResource()
        with (
            patch.object(storage.resource_adapters, "for_resource", return_value=adapter),
            patch.object(storage, "bump_resource_generation") as bump,
            patch(
                "evennia.server.prometheus_metrics.record_authorization_move_invalidation"
            ) as record,
        ):
            self.assertTrue(storage.bump_resource_generation_after_move(resource))
        bump.assert_called_once_with(resource)
        record.assert_called_once_with("bumped")

    def test_adapter_lookup_failure_fails_safe(self):
        from evennia.authorization import storage

        resource = FakeResource()
        with (
            patch.object(
                storage.resource_adapters, "for_resource", side_effect=RuntimeError("boom")
            ),
            patch.object(storage, "bump_resource_generation") as bump,
            patch.object(storage, "logger") as logger,
            patch(
                "evennia.server.prometheus_metrics.record_authorization_move_invalidation"
            ) as record,
        ):
            self.assertTrue(storage.bump_resource_generation_after_move(resource))
        bump.assert_called_once_with(resource)
        record.assert_called_once_with("lookup_error")
        logger.log_trace.assert_called_once()
