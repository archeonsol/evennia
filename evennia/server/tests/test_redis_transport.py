"""Bounded delivery tests with independently controlled callback execution."""

import threading
import time
from collections import deque
from unittest.mock import patch

import fakeredis
from django.test import SimpleTestCase

from evennia.server import redis_transport as transport_module
from evennia.server.redis_transport import RedisTransport


class TestRedisTransport(SimpleTestCase):
    """Pause callbacks independently from real reader and writer threads."""

    def setUp(self):
        """Use isolated Redis state and capture loop handoffs."""
        self.callbacks = deque()
        self.client = fakeredis.FakeRedis()
        self.delivered = []
        self.failures = []
        self.transport = RedisTransport(
            "in", lambda *args: self.delivered.append(args), on_failure=self.failures.append
        )
        client_patch = patch("redis.Redis.from_url", return_value=self.client)
        clock_patch = patch.object(
            transport_module.clock,
            "call_from_thread",
            side_effect=lambda fn, *args: self.callbacks.append((fn, args)),
        )
        client_patch.start()
        clock_patch.start()
        self.addCleanup(client_patch.stop)
        self.addCleanup(clock_patch.stop)
        self.addCleanup(self.transport.stop)

    def pump(self, predicate=lambda: False, seconds=1):
        """Run callbacks until a condition or deadline."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.callbacks:
                fn, args = self.callbacks.popleft()
                fn(*args)
            elif predicate():
                return
            else:
                time.sleep(0.005)

    def test_old_history_is_not_delivered(self):
        """A new reader captures the tail before discovery."""
        self.client.xadd("in", {b"c": b"Msg", b"d": b"old"})
        self.transport.start()
        self.pump(seconds=0.05)
        self.assertFalse(self.delivered)

    def test_committed_write_reply_loss_never_republishes(self):
        """Lost XADD replies fail after one committed write."""
        self.transport.start()
        xadd = self.client.xadd
        calls = []

        def lost(*args, **kwargs):
            calls.append(args)
            xadd(*args, **kwargs)
            raise ConnectionError("reply lost")

        with patch.object(self.client, "xadd", side_effect=lost):
            result = self.transport.publish("out", b"Msg", b"action")
            self.pump(lambda: bool(self.failures))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.client.xlen("out"), 1)
        self.assertIsNotNone(result._future.exception())

    def test_unsettled_results_hold_capacity(self):
        """A stalled loop cannot release capacity through worker callbacks."""
        self.transport.start()
        with (
            patch.object(transport_module, "MAX_ENTRIES", 4),
            patch.object(transport_module, "DATA_ENTRIES", 3),
        ):
            results = [self.transport.publish("out", b"Msg", b"a") for _ in range(3)]
            time.sleep(0.05)
            self.assertFalse(self.transport.publish("out", b"Msg", b"b").admitted)
            self.assertEqual(self.transport.outgoing_count, 3)
            self.assertTrue(all(result.admitted for result in results))
            self.pump(lambda: self.transport.outgoing_count == 0)
            self.assertTrue(self.transport.publish("out", b"Msg", b"new").admitted)

    def test_control_saturation_can_recover(self):
        """Recovery needs no slot in the failed queue."""
        self.transport.start()
        with (
            patch.object(transport_module, "MAX_ENTRIES", 2),
            patch.object(transport_module, "DATA_ENTRIES", 1),
        ):
            self.transport.publish("out", b"Admin", b"one")
            self.transport.publish("out", b"Admin", b"two")
            self.assertFalse(self.transport.publish("out", b"Admin", b"three").admitted)
            self.assertFalse(self.transport.online)
            self.pump(lambda: self.transport.online)
            self.assertTrue(self.failures)
            self.assertTrue(self.transport.online)

    def test_queued_callback_is_invalidated_before_notification(self):
        """A scheduled drain cannot execute a failed generation."""
        self.transport.start()
        self.transport._admit_incoming({b"c": b"Msg", b"d": b"old", b"g": b""})
        self.transport.fail("read gap")
        self.pump(lambda: bool(self.failures))
        self.assertFalse(self.delivered)

    def test_oversized_frame_rejects(self):
        """Count limits cannot hide large encoded payloads."""
        self.transport.start()
        with patch.object(transport_module, "MAX_FRAME_BYTES", 10):
            self.assertFalse(self.transport.publish("out", b"Msg", b"a" * 11).admitted)
        self.assertEqual(self.transport.outgoing_count, 0)

    def test_failure_observer_cannot_block_settlement_or_recovery(self):
        """One faulty observer cannot keep an entire failed generation alive."""
        self.transport.online = True
        first = self.transport.publish("out", b"Msg", b"one")
        second = self.transport.publish("out", b"Msg", b"two")

        def broken(_error):
            raise ValueError("observer failed")

        first.addErrback(broken)
        self.transport.fail("outage")
        with patch.object(transport_module.logger, "log_trace") as log:
            fn, args = self.callbacks.popleft()
            fn(*args)
        log.assert_called_once()
        self.assertTrue(first._future.done())
        self.assertTrue(second._future.done())
        self.assertTrue(self.transport._failure_delivered)
        self.assertEqual(self.transport.outgoing_count, 0)
        self.transport._recovered(self.transport._fence)
        self.assertTrue(self.transport.publish("out", b"Msg", b"fresh").admitted)

    def test_handler_failure_does_not_wedge_fresh_delivery(self):
        """An application exception fences old frames and releases the drain."""
        self.transport.online = True

        def broken(*_args):
            raise ValueError("handler failed")

        self.transport._on_frame = broken
        self.transport._admit_incoming({b"c": b"Msg", b"d": b"bad"})
        self.transport._admit_incoming({b"c": b"Msg", b"d": b"discard"})
        with patch.object(transport_module.logger, "log_trace") as log:
            fn, args = self.callbacks.popleft()
            fn(*args)
        log.assert_called_once()
        self.assertFalse(self.transport.online)
        self.assertFalse(self.transport._drain_scheduled)
        self.assertEqual(self.transport._incoming_bytes, 0)
        fn, args = self.callbacks.popleft()
        fn(*args)
        self.transport._on_frame = lambda *args: self.delivered.append(args)
        self.transport._recovered(self.transport._fence)
        self.transport._admit_incoming({b"c": b"Msg", b"d": b"fresh"})
        fn, args = self.callbacks.popleft()
        fn(*args)
        self.assertEqual(self.delivered, [(b"Msg", b"fresh")])

    def test_restart_rejects_unsettled_failure_notification(self):
        """Old queued failure notifications cannot affect a newly started run."""
        self.transport.online = True
        self.transport.stop()
        with self.assertRaises(RuntimeError):
            self.transport.start()
        fn, args = self.callbacks.popleft()
        fn(*args)
        self.assertTrue(self.transport.start())

    def test_first_writer_sequence_must_start_at_one(self):
        """Trimming after an empty baseline is detected before callback dispatch."""
        self.transport.start()
        self.client.xadd("in", {b"c": b"Msg", b"d": b"lost prefix", b"o": b"writer", b"n": b"500"})
        self.pump(lambda: bool(self.failures))
        self.assertIn("transport stream sequence gap", self.failures)
        self.assertEqual(self.delivered, [])

    def test_captured_baseline_allows_existing_writer_sequence(self):
        """Starting at an existing tail does not demand a writer reset."""
        self.client.xadd("in", {b"c": b"Msg", b"d": b"old", b"o": b"writer", b"n": b"500"})
        self.transport.start()
        self.client.xadd("in", {b"c": b"Msg", b"d": b"new", b"o": b"writer", b"n": b"501"})
        self.pump(lambda: bool(self.delivered))
        self.assertEqual(self.delivered, [(b"Msg", b"new")])
        self.assertEqual(self.failures, [])

    def test_drain_yields_after_one_bounded_batch(self):
        """A pending unrelated loop callback runs before the next input batch."""
        self.transport.online = True
        for index in range(40):
            self.transport._admit_incoming({b"c": b"Msg", b"d": str(index).encode()})
        observed = []
        self.callbacks.append((lambda: observed.append(len(self.delivered)), ()))
        self.assertEqual(len(self.callbacks), 2)
        fn, args = self.callbacks.popleft()
        fn(*args)
        self.assertEqual(len(self.delivered), 32)
        fn, args = self.callbacks.popleft()
        fn(*args)
        self.assertEqual(observed, [32])
        fn, args = self.callbacks.popleft()
        fn(*args)
        self.assertEqual(len(self.delivered), 40)
        self.assertEqual(self.transport._incoming_bytes, 0)

    def test_active_frame_remains_in_incoming_byte_budget(self):
        """A reader cannot replace an active callback's still-live payload."""
        self.transport.online = True
        fields = {b"c": b"Msg", b"d": b"payload"}
        size = sum(len(key) + len(value) for key, value in fields.items())
        observed = []

        def during_callback(*_args):
            observed.append(self.transport._incoming_bytes)
            self.transport._admit_incoming(fields)

        self.transport._on_frame = during_callback
        with patch.object(transport_module, "DATA_BYTES", size):
            self.transport._admit_incoming(fields)
            fn, args = self.callbacks.popleft()
            fn(*args)
        self.assertEqual(observed, [size])
        self.assertFalse(self.transport.online)
        self.assertEqual(self.transport._incoming_bytes, 0)
        self.assertFalse(self.transport._incoming)

    def test_pair_is_checked_again_before_execution(self):
        """A ready queue does not authorize work after the peer pair changes."""
        self.transport.online = True
        self.transport._admit_incoming({b"c": b"Msg", b"d": b"old"})
        self.transport.set_pair(["new", "pair"], ready=True)
        fn, args = self.callbacks.popleft()
        fn(*args)
        self.assertEqual(self.delivered, [])

    def test_stop_keeps_inflight_write_accounted_without_waiting_for_loop(self):
        """A stuck publication stays owned until its worker returns."""
        entered = threading.Event()
        release = threading.Event()

        def stuck(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return b"1-0"

        self.transport.start()
        with (
            patch.object(self.client, "xadd", side_effect=stuck),
            patch.object(transport_module, "STOP_TIMEOUT", 0.05),
        ):
            result = self.transport.publish("out", b"Msg", b"action")
            try:
                self.assertTrue(entered.wait(1))
                started = time.monotonic()
                self.transport.stop()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(self.transport.outgoing_count, 1)
                fn, args = self.callbacks.popleft()
                fn(*args)
                self.assertEqual(self.transport.outgoing_count, 1)
                self.assertIsNotNone(result._future.exception())
            finally:
                release.set()
            self.pump(lambda: self.transport.outgoing_count == 0)
        self.assertEqual(self.transport.outgoing_count, 0)
