"""Opt-in real Redis and TCP faults for the bounded transport boundary.

Set EVENNIA_REDIS_SERVER to a Redis server executable. No production Redis is used.
The event-loop queue is controlled explicitly; Redis clients and sockets are real.
"""

import queue
import time
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from evennia.server import redis_transport as transport_module
from evennia.server.portal import amp
from evennia.server.redis_transport import RedisTransport
from evennia.server.tests.redis_live_helpers import (
    REDIS_SERVER,
    RedisProcess,
    ReplyLossProxy,
    await_condition,
)


@skipUnless(REDIS_SERVER, "set EVENNIA_REDIS_SERVER for real Redis fault tests")
class TestLiveRedisTransport(SimpleTestCase):
    """Observe actual stream contents independently of transport completion."""

    def setUp(self):
        """Own a private Redis instance and capture event-loop handoffs."""
        self.callbacks = queue.Queue()
        self.delivered = []
        self.failures = []
        self.enterContext(
            patch.object(
                transport_module.clock,
                "call_from_thread",
                side_effect=lambda fn, *args: self.callbacks.put((fn, args)),
            )
        )
        self.redis = RedisProcess()
        self.addCleanup(self.redis.close)

    def transport(self, url=None):
        """Register transport cleanup before exposing the test connection."""
        with override_settings(REDIS_BUS_URL=url or self.redis.url):
            transport = RedisTransport(
                "input",
                lambda *args: self.delivered.append(args),
                on_failure=self.failures.append,
            )
        self.addCleanup(transport.stop)
        return transport

    def pump_until(self, predicate, seconds=5):
        """Execute captured event-loop callbacks under a fixed test deadline."""

        def step():
            """Execute one handoff before checking the requested condition."""
            try:
                fn, args = self.callbacks.get_nowait()
            except queue.Empty:
                return predicate()
            fn(*args)
            return predicate()

        await_condition(step, seconds)

    def append(self, index, payload=b"action"):
        """Publish a sequenced peer frame through the independent Redis client."""
        return self.redis.client.xadd(
            "input",
            {b"c": b"Msg", b"d": payload, b"o": b"peer", b"n": str(index).encode()},
        )

    def test_committed_xadd_socket_reply_loss_does_not_retry(self):
        """Redis commits, proxy consumes its reply, and redis-py sees a dead socket."""
        self.assert_reply_loss_once(b"Msg", b"single action")

    def test_committed_lifecycle_socket_reply_loss_does_not_retry(self):
        """A committed reload request receives the same no-republish guarantee."""
        self.assert_reply_loss_once(
            b"AdminPortal2Server", amp.dumps_admin((0, {"operation": amp.SRELOAD}))
        )

    def assert_reply_loss_once(self, command, payload):
        """Inspect real stream state after an actual response socket is closed."""
        proxy = ReplyLossProxy(self.redis.port)
        self.addCleanup(proxy.close)
        transport = self.transport(proxy.url)
        self.assertTrue(transport.start())
        result = transport.publish("output", command, payload)
        await_condition(proxy.dropped.is_set)
        self.pump_until(lambda: result._future.done())
        self.assertIsNotNone(result._future.exception())
        self.pump_until(lambda: transport.online)
        # Recovery and fresh connections must not create a second XADD.
        self.assertEqual(proxy.xadds, 1)
        entries = self.redis.client.xrange("output")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1][b"c"], command)
        self.assertEqual(entries[0][1][b"d"], payload)
        self.assertEqual(transport.outgoing_count, 0)
        self.assertFalse(proxy.errors)

    def test_redis_stop_with_both_direction_queues_full(self):
        """Full data and reserved control capacity cannot prevent bounded stop."""
        transport = self.transport()
        self.assertTrue(transport.start())
        with patch.multiple(
            transport_module,
            MAX_ENTRIES=4,
            DATA_ENTRIES=3,
            MAX_BYTES=1600,
            DATA_BYTES=1000,
        ):
            results = [transport.publish("output", b"Msg", b"out") for _ in range(3)]
            results.append(transport.publish("output", b"AdminServer2Portal", b"control"))
            for index in range(1, 4):
                self.append(index)
            self.redis.client.xadd(
                "input",
                {
                    b"c": b"AdminPortal2Server",
                    b"d": b"control",
                    b"o": b"peer",
                    b"n": b"4",
                },
            )
            await_condition(
                lambda: len(transport._incoming) == 4 and self.redis.client.xlen("output") == 4
            )
            self.assertEqual(transport.outgoing_count, 4)
            self.assertTrue(all(result.admitted for result in results))
            self.assertTrue(all(not result._future.done() for result in results))
            self.assertLessEqual(transport.outgoing_bytes, 1600)
            self.assertLessEqual(transport._incoming_bytes, 1600)
            self.assertEqual(self.delivered, [])
            self.redis.stop()
            started = time.monotonic()
            transport.stop()
            self.assertLess(time.monotonic() - started, transport_module.STOP_TIMEOUT + 0.5)
            transport.settle_failure()
            self.pump_until(self.callbacks.empty)
            self.assertTrue(all(result._future.done() for result in results))
            self.assertTrue(all(result._future.exception() is not None for result in results))
            self.assertEqual(transport.outgoing_count, 0)
            self.assertEqual(transport.outgoing_bytes, 0)
            self.assertEqual(transport._incoming_bytes, 0)
            self.assertEqual(self.delivered, [])

    def test_outgoing_count_and_bytes_remain_bounded_through_redis_stop(self):
        """A paused event loop retains completed writes in its capacity budget."""
        transport = self.transport()
        self.assertTrue(transport.start())
        with patch.multiple(
            transport_module,
            MAX_ENTRIES=4,
            DATA_ENTRIES=3,
            MAX_BYTES=1600,
            DATA_BYTES=1000,
        ):
            results = [transport.publish("output", b"Msg", b"a") for _ in range(3)]
            await_condition(lambda: self.redis.client.xlen("output") == 3)
            self.assertEqual(transport.outgoing_count, 3)
            self.assertLessEqual(transport.outgoing_bytes, 1000)
            self.assertFalse(transport.publish("output", b"Msg", b"overflow").admitted)
            self.pump_until(lambda: transport.outgoing_count == 0)
            self.assertTrue(all(item._future.exception() is None for item in results))
            result = transport.publish("output", b"Msg", b"a" * 600)
            self.assertTrue(result.admitted)
            self.assertFalse(transport.publish("output", b"Msg", b"b" * 600).admitted)
            self.assertLessEqual(transport.outgoing_bytes, 1000)
            self.redis.stop()
            started = time.monotonic()
            transport.stop()
            self.assertLess(time.monotonic() - started, transport_module.STOP_TIMEOUT + 0.5)
            transport.settle_failure()
            self.assertTrue(result._future.done())
            self.assertLessEqual(transport.outgoing_count, 4)
            self.assertLessEqual(transport.outgoing_bytes, 1600)

    def test_incoming_count_saturation_invalidates_queued_callbacks(self):
        """Real XREAD admission cannot queue unbounded work behind a stalled loop."""
        transport = self.transport()
        self.assertTrue(transport.start())
        with patch.multiple(
            transport_module,
            MAX_ENTRIES=4,
            DATA_ENTRIES=3,
            MAX_BYTES=1600,
            DATA_BYTES=1000,
        ):
            for index in range(1, 4):
                self.append(index)
            await_condition(lambda: len(transport._incoming) == 3)
            self.assertLessEqual(transport._incoming_bytes, 1000)
            self.append(4)
            await_condition(lambda: not transport.online)
            self.assertFalse(transport._incoming)
            self.assertEqual(transport._incoming_bytes, 0)
            self.pump_until(lambda: bool(self.failures))
            self.assertEqual(self.delivered, [])
            self.assertIn("incoming callback capacity exhausted", self.failures)

    def test_incoming_byte_saturation_recovers_without_old_actions(self):
        """The encoded-byte limit fails before the larger entry limit is reached."""
        transport = self.transport()
        self.assertTrue(transport.start())
        with patch.multiple(
            transport_module,
            MAX_ENTRIES=4,
            DATA_ENTRIES=3,
            MAX_BYTES=1600,
            DATA_BYTES=1000,
        ):
            self.append(1, b"a" * 600)
            await_condition(lambda: len(transport._incoming) == 1)
            self.assertLessEqual(transport._incoming_bytes, 1000)
            self.append(2, b"b" * 600)
            await_condition(lambda: not transport.online)
            self.pump_until(lambda: bool(self.failures) and transport.online)
            self.assertEqual(self.delivered, [])
            self.append(3, b"fresh")
            self.pump_until(lambda: bool(self.delivered))
            self.assertEqual(self.delivered, [(b"Msg", b"fresh")])

    def test_trimmed_unread_history_fails_then_establishes_fresh_baseline(self):
        """Actual XTRIM cannot silently skip a retained-history gap."""
        for index in range(1, 4):
            self.append(index)
        transport = self.transport()
        with transport._lock:
            self.assertTrue(transport.start())
            pipeline = self.redis.client.pipeline()
            for index in range(4, 21):
                pipeline.xadd(
                    "input",
                    {
                        b"c": b"Msg",
                        b"d": b"old",
                        b"o": b"peer",
                        b"n": str(index).encode(),
                    },
                )
            pipeline.xtrim("input", maxlen=1, approximate=False)
            pipeline.execute()
        await_condition(lambda: not transport.online)
        self.pump_until(lambda: bool(self.failures) and transport.online)
        self.assertEqual(self.delivered, [])
        self.assertTrue(
            any(
                "history was trimmed" in error or "sequence gap" in error for error in self.failures
            )
        )
        self.append(21, b"fresh")
        self.pump_until(lambda: bool(self.delivered))
        self.assertEqual(self.delivered, [(b"Msg", b"fresh")])
