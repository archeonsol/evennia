"""Tests for push-based authorization invalidation."""

import asyncio
import time
from contextlib import ExitStack
from unittest.mock import patch

import fakeredis
from django.test import SimpleTestCase, override_settings

from evennia.authorization import invalidation, storage


class InvalidationTestBase(SimpleTestCase):
    """Shared fakeredis + push-enabled settings with reader isolation."""

    def setUp(self):
        self.fake = fakeredis.FakeRedis(decode_responses=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("django_redis.get_redis_connection", return_value=self.fake))
        self.stack.enter_context(
            override_settings(
                AUTHORIZATION_OFFLOOP_SNAPSHOTS=True,
                AUTHORIZATION_PUSH_INVALIDATION=True,
                AUTHORIZATION_INVALIDATION_CONSUMER="test",
                AUTHORIZATION_INVALIDATION_READ_BLOCK_MS=50,
                AUTHORIZATION_INVALIDATION_RECONCILE_SECONDS=3600.0,
            )
        )
        invalidation.stop()
        self.addCleanup(invalidation.stop)
        storage.clear_authorization_caches()
        self.addCleanup(storage.clear_authorization_caches)


class PublishTest(InvalidationTestBase):
    def test_publish_appends_revisioned_event(self):
        first = invalidation.publish_generation("resource", "object:7", 3)
        second = invalidation.publish_generation("principal", "account:1", 5)

        self.assertEqual((first, second), (1, 2))
        entries = self.fake.xrange(invalidation._stream_key())
        self.assertEqual(len(entries), 2)
        _entry_id, fields = entries[0]
        self.assertEqual(fields[b"ns"], b"resource")
        self.assertEqual(fields[b"ref"], b"object:7")
        self.assertEqual(fields[b"g"], b"3")
        self.assertEqual(fields[b"r"], b"1")

    def test_publish_failure_is_swallowed(self):
        with patch("django_redis.get_redis_connection", side_effect=RuntimeError("down")):
            self.assertIsNone(invalidation.publish_generation("resource", "object:1", 1))


class ApplyTest(InvalidationTestBase):
    def test_resource_event_drops_resource_and_policy_snapshots(self):
        storage._resource_cache["object:7"] = object()
        storage._policy_package_cache["object:7"] = (1, {})
        storage._resource_cache["object:8"] = object()

        invalidation.apply_events(
            [
                (
                    "1-0",
                    {b"r": b"1", b"ns": b"resource", b"ref": b"object:7", b"g": b"4"},
                )
            ]
        )

        self.assertNotIn("object:7", storage._resource_cache)
        self.assertNotIn("object:7", storage._policy_package_cache)
        self.assertIn("object:8", storage._resource_cache)
        self.assertEqual(storage._resource_generation["object:7"], 4)
        cache_key = storage._generation_cache_key("resource", "object:7")
        self.assertEqual(storage._shared_generation_cache[cache_key][1], 4)
        self.assertEqual(invalidation.applied_revision(), 1)

    def test_principal_event_clears_grant_and_suspension_caches(self):
        storage._principal_cache["account:1"] = object()
        storage._suspension_cache["account:1"] = (1, False, None)

        invalidation.apply_events(
            [
                (
                    "1-0",
                    {b"r": b"1", b"ns": b"principal", b"ref": b"account:1", b"g": b"2"},
                )
            ]
        )

        self.assertEqual(storage._principal_cache, {})
        self.assertEqual(storage._suspension_cache, {})
        self.assertEqual(storage._principal_generation["account:1"], 2)

    def test_generations_apply_monotonically(self):
        invalidation.apply_events(
            [
                (
                    "1-0",
                    {b"r": b"1", b"ns": b"resource", b"ref": b"object:7", b"g": b"9"},
                )
            ]
        )
        invalidation.apply_events(
            [
                (
                    "2-0",
                    {b"r": b"2", b"ns": b"resource", b"ref": b"object:7", b"g": b"3"},
                )
            ]
        )

        self.assertEqual(storage._resource_generation["object:7"], 9)

    def test_gap_reconciles_and_keeps_applying(self):
        with patch.object(storage, "clear_authorization_caches") as reconcile:
            invalidation.apply_events(
                [
                    (
                        "1-0",
                        {b"r": b"1", b"ns": b"resource", b"ref": b"object:7", b"g": b"1"},
                    )
                ]
            )
            invalidation.apply_events(
                [
                    (
                        "2-0",
                        {b"r": b"5", b"ns": b"resource", b"ref": b"object:7", b"g": b"5"},
                    )
                ]
            )

        reconcile.assert_called_once()
        self.assertEqual(invalidation.applied_revision(), 5)
        self.assertEqual(storage._resource_generation["object:7"], 5)

    def test_malformed_event_is_dropped_without_stalling_the_cursor(self):
        invalidation.apply_events([("7-0", {b"r": b"nope", b"ns": b"resource"})])

        self.assertEqual(invalidation._cursor_id, "7-0")
        self.assertEqual(invalidation.applied_revision(), 0)


class CursorTest(InvalidationTestBase):
    def test_cursor_roundtrip(self):
        invalidation._persist_cursor_io("12-3", 44)

        self.assertEqual(invalidation._load_cursor(), ("12-3", 44))
        with override_settings(AUTHORIZATION_INVALIDATION_CONSUMER="other"):
            self.assertIsNone(invalidation._load_cursor())

    def test_baseline_prefers_persisted_cursor(self):
        invalidation._persist_cursor_io("9-9", 7)

        self.assertEqual(invalidation._baseline_io(), ("9-9", 7))

    def test_baseline_without_cursor_uses_stream_head_and_revision(self):
        invalidation.publish_generation("resource", "object:1", 1)
        self.fake.set(invalidation._revision_key(), 1)

        cursor_id, revision = invalidation._baseline_io()

        self.assertEqual(revision, 1)
        self.assertNotEqual(cursor_id, "0-0")


class AntiEntropyTest(InvalidationTestBase):
    def test_revision_ahead_reconciles_after_a_second_check(self):
        with patch.object(storage, "clear_authorization_caches") as reconcile:
            invalidation._apply_revision_check(11)
            # The first observation may simply race the reader.
            reconcile.assert_not_called()
            invalidation._apply_revision_check(11)

        reconcile.assert_called_once()
        self.assertEqual(invalidation.applied_revision(), 11)

    def test_reader_catching_up_clears_the_pending_check(self):
        with patch.object(storage, "clear_authorization_caches") as reconcile:
            invalidation._apply_revision_check(11)
            invalidation._applied_revision = 11
            invalidation._apply_revision_check(11)

        reconcile.assert_not_called()

    def test_revision_current_does_not_reconcile(self):
        with patch.object(storage, "clear_authorization_caches") as reconcile:
            invalidation._apply_revision_check(0)

        reconcile.assert_not_called()


class TwoProcessSimulationTest(InvalidationTestBase):
    def test_event_published_by_one_process_drops_another_process_facts(self):
        # Process A: applies locally, then appends to the durable stream.
        storage._resource_generation["object:9"] = 0
        storage._resource_cache["object:9"] = object()
        storage._policy_package_cache["object:9"] = (0, {})

        revision = invalidation.publish_generation("resource", "object:9", 1)

        self.assertEqual(revision, 1)
        # Process B: reads the stream from its cursor and applies.
        entries = invalidation._read_batch("0-0")
        self.assertEqual(len(entries), 1)
        invalidation.apply_events(entries)

        self.assertNotIn("object:9", storage._resource_cache)
        self.assertNotIn("object:9", storage._policy_package_cache)
        self.assertEqual(storage._resource_generation["object:9"], 1)

    def test_cursor_replay_skips_consumed_events(self):
        invalidation.publish_generation("resource", "object:1", 1)
        first = invalidation._read_batch("0-0")
        cursor = first[-1][0]

        invalidation.publish_generation("resource", "object:2", 1)
        second = invalidation._read_batch(cursor)

        self.assertEqual(len(second), 1)
        self.assertEqual(second[0][1][b"ref"], b"object:2")


class ReaderThreadTest(InvalidationTestBase):
    def test_reader_applies_events_and_reconnects_after_errors(self):
        batches = []
        failures = []

        def fake_read(cursor):
            if not batches:
                batches.append(True)
                return [
                    (
                        "1-0",
                        {b"r": b"1", b"ns": b"resource", b"ref": b"object:5", b"g": b"2"},
                    )
                ]
            if not failures:
                failures.append(True)
                raise RuntimeError("transient")
            invalidation._reader_stop.wait(0.05)
            return []

        with patch.object(invalidation, "_read_batch", side_effect=fake_read):
            self.assertTrue(invalidation.start())
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and "object:5" not in storage._resource_generation:
                time.sleep(0.02)

        self.assertEqual(storage._resource_generation.get("object:5"), 2)
        self.assertTrue(failures)

    def test_start_is_disabled_without_the_setting(self):
        with override_settings(AUTHORIZATION_PUSH_INVALIDATION=False):
            self.assertFalse(invalidation.start())


class EnsureAuthorizationTest(InvalidationTestBase):
    def test_ready_path_returns_without_fetching(self):
        with (
            patch.object(invalidation, "start", return_value=True),
            patch.object(storage, "_prewarm_request", return_value=(True, {})),
            patch.object(storage, "_fetch_authorization_snapshots") as fetch,
        ):
            self.assertTrue(asyncio.run(storage.ensure_authorization((), ())))

        fetch.assert_not_called()

    def test_cold_fact_costs_one_coalesced_fetch(self):
        state = {"fetched": False}

        def request(*args, **kwargs):
            return state["fetched"], {}

        async def flush():
            state["fetched"] = True
            return True

        with (
            patch.object(invalidation, "start", return_value=True),
            patch.object(storage, "_prewarm_request", side_effect=request),
            patch.object(storage, "_flush_authorization_prewarm", side_effect=flush),
        ):
            self.assertTrue(asyncio.run(storage.ensure_authorization((object(),), (object(),))))

    def test_cold_fact_failure_returns_false(self):
        async def flush():
            return False

        with (
            patch.object(invalidation, "start", return_value=True),
            patch.object(storage, "_prewarm_request", return_value=(False, {})),
            patch.object(storage, "_flush_authorization_prewarm", side_effect=flush),
        ):
            self.assertFalse(asyncio.run(storage.ensure_authorization((object(),), (object(),))))

    @override_settings(AUTHORIZATION_PUSH_INVALIDATION=False)
    def test_without_push_delegates_to_prewarm(self):
        with patch.object(storage, "prewarm_authorization", return_value=True) as prewarm:
            self.assertTrue(asyncio.run(storage.ensure_authorization((), ())))

        prewarm.assert_called_once()
