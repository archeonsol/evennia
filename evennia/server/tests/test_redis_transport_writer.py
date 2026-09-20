"""Batched writer and capacity backpressure for the Redis transport.

These run without a real Redis: a fake client records the pipeline the writer
builds, and the captured event-loop callbacks are executed by hand. The live
fault matrix stays in ``test_redis_transport_live.py``.
"""

import queue
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from evennia.server import redis_transport as transport_module
from evennia.server.redis_transport import RedisTransport


class _FakePipeline:
    """Record queued XADDs and answer with synthetic entry ids."""

    def __init__(self):
        self.calls = []
        self.executed = 0

    def xadd(self, stream, fields, maxlen=None, approximate=None):
        self.calls.append((stream, dict(fields)))
        return self

    def execute(self, raise_on_error=False):
        self.executed += 1
        return [f"{index + 1}-0" for index in range(len(self.calls))]


class _FakeClient:
    """Minimal client exposing only the pipeline surface the writer uses."""

    def __init__(self):
        self.pipelines = []

    def pipeline(self, transaction=False):
        pipeline = _FakePipeline()
        self.pipelines.append(pipeline)
        return pipeline


class _PartialFailurePipeline(_FakePipeline):
    """Answer execute() with one per-command error and the rest entry ids."""

    def execute(self, raise_on_error=False):
        self.executed += 1
        return [
            RuntimeError("xadd rejected") if index == 0 else f"{index}-0"
            for index in range(len(self.calls))
        ]


_LIMIT_CASES = (
    ("REDIS_BUS_MAX_ENTRIES", "max_entries", "MAX_ENTRIES"),
    ("REDIS_BUS_MAX_BYTES", "max_bytes", "MAX_BYTES"),
    ("REDIS_BUS_DATA_ENTRIES", "data_entries", "DATA_ENTRIES"),
    ("REDIS_BUS_DATA_BYTES", "data_bytes", "DATA_BYTES"),
    ("REDIS_BUS_WRITE_BATCH", "write_batch", "WRITE_BATCH_SIZE"),
    ("REDIS_BUS_READ_BATCH", "read_batch", "READ_BATCH"),
)


class BusLimitsTest(SimpleTestCase):
    """One limits object resolves an explicit setting over a patchable constant."""

    def test_each_setting_wins_over_the_module_constant(self):
        values = {name: 7 + index for index, (name, _f, _c) in enumerate(_LIMIT_CASES)}
        with override_settings(**values):
            limits = transport_module.resolve_limits()
        for index, (_name, field, _const) in enumerate(_LIMIT_CASES):
            with self.subTest(field=field):
                self.assertEqual(getattr(limits, field), 7 + index)

    def test_patched_constants_back_stop_and_pair_the_classes(self):
        with override_settings(**{name: None for name, _f, _c in _LIMIT_CASES}):
            with patch.multiple(
                transport_module,
                MAX_ENTRIES=11,
                MAX_BYTES=12,
                DATA_ENTRIES=13,
                DATA_BYTES=14,
                WRITE_BATCH_SIZE=15,
                READ_BATCH=16,
            ):
                limits = transport_module.resolve_limits()
        self.assertEqual(limits.limit_pair(True), (11, 12))
        self.assertEqual(limits.limit_pair(False), (13, 14))
        self.assertEqual(limits.write_batch, 15)
        self.assertEqual(limits.read_batch, 16)

    def test_explicit_settings_are_clamped_to_positive(self):
        with override_settings(REDIS_BUS_DATA_ENTRIES=0):
            self.assertEqual(transport_module.resolve_limits().data_entries, 1)


class _RaisingMetrics:
    """Telemetry stub whose every hot-path call raises."""

    def record_bus_write_batch(self, size):
        raise RuntimeError("telemetry down")

    def observe_bus_queue(self, depth, size):
        raise RuntimeError("telemetry down")

    def record_bus_publish(self, count=1):
        raise RuntimeError("telemetry down")

    def record_bus_reject(self, reason):
        raise RuntimeError("telemetry down")


class WriterBatchTest(SimpleTestCase):
    """One Redis round trip per batch, in wire order, capacity as backpressure."""

    def setUp(self):
        self.callbacks = queue.Queue()
        self.failures = []
        self.enterContext(
            patch.object(
                transport_module.clock,
                "call_from_thread",
                side_effect=lambda fn, *args, **kwargs: self.callbacks.put((fn, args)),
            )
        )

    def _transport(self):
        transport = RedisTransport("input", lambda *args: None, on_failure=self.failures.append)
        transport.online = True
        transport._ready = True
        transport._client = _FakeClient()
        return transport

    def _run_callbacks(self):
        while True:
            try:
                fn, args = self.callbacks.get_nowait()
            except queue.Empty:
                return
            fn(*args)

    def test_writer_publishes_in_batches_with_ordered_sequences(self):
        transport = self._transport()
        with patch.multiple(transport_module, WRITE_BATCH_SIZE=2):
            for _ in range(5):
                self.assertTrue(transport.publish("output", b"Msg", b"x").admitted)

            batches = []
            while transport._ordinary:
                batch = transport._take_write_batch()
                self.assertTrue(batch)
                batches.append(batch)
                transport._write_batch(batch)

            self.assertEqual([len(batch) for batch in batches], [2, 2, 1])
            client = transport._client
            self.assertEqual(len(client.pipelines), 3)
            sequences = [
                int(fields[b"n"])
                for pipeline in client.pipelines
                for _stream, fields in pipeline.calls
            ]
            self.assertEqual(sequences, [1, 2, 3, 4, 5])
            streams = [
                stream for pipeline in client.pipelines for stream, _fields in pipeline.calls
            ]
            self.assertEqual(streams, ["output"] * 5)

            self._run_callbacks()
            self.assertEqual(transport.outgoing_count, 0)
            self.assertEqual(self.failures, [])

    def test_handshake_frames_are_isolated_from_data_batches(self):
        transport = self._transport()
        with patch.multiple(transport_module, WRITE_BATCH_SIZE=4):
            for _ in range(3):
                self.assertTrue(transport.publish("output", b"Msg", b"x").admitted)
            self.assertTrue(transport.publish("output", b"BusProbe", b"h", handshake=True).admitted)
            first = transport._take_write_batch()
            self.assertEqual([frame.command for _key, frame, _fields in first], [b"BusProbe"])
            second = transport._take_write_batch()
            self.assertEqual(len(second), 3)

    def test_outgoing_bytes_tracks_admission_and_settlement(self):
        transport = self._transport()
        self.assertTrue(transport.publish("output", b"Msg", b"x").admitted)
        after_one = transport.outgoing_bytes
        self.assertGreater(after_one, 0)
        self.assertTrue(transport.publish("output", b"Msg", b"x").admitted)
        self.assertGreater(transport.outgoing_bytes, after_one)
        transport._write_batch(transport._take_write_batch())
        self._run_callbacks()
        self.assertEqual(transport.outgoing_bytes, 0)
        self.assertEqual(transport.outgoing_count, 0)

    def test_publish_flood_rejects_backpressure_without_failing(self):
        """A queue-full flood rejects locally and settles cleanly once drained."""
        transport = self._transport()
        with patch.multiple(
            transport_module,
            WRITE_BATCH_SIZE=64,
            MAX_ENTRIES=128,
            DATA_ENTRIES=64,
            MAX_BYTES=10**9,
            DATA_BYTES=10**9,
            OUTGOING_WARN_THRESHOLD=10**9,
        ):
            admitted = rejected = 0
            for _ in range(500):
                if transport.publish("output", b"Msg", b"x" * 32).admitted:
                    admitted += 1
                else:
                    rejected += 1
            self.assertGreater(admitted, 0)
            self.assertGreater(rejected, 0)
            self.assertTrue(transport.online)
            self.assertEqual(self.failures, [])
            while transport._ordinary:
                transport._write_batch(transport._take_write_batch())
            self._run_callbacks()
            self.assertEqual(transport.outgoing_count, 0)
            self.assertEqual(transport.outgoing_bytes, 0)

    def test_capacity_pressure_rejects_without_failing_the_transport(self):
        transport = self._transport()
        with patch.multiple(
            transport_module,
            MAX_ENTRIES=3,
            DATA_ENTRIES=2,
            MAX_BYTES=10_000,
            DATA_BYTES=10_000,
        ):
            self.assertTrue(transport.publish("output", b"Msg", b"a").admitted)
            self.assertTrue(transport.publish("output", b"Msg", b"b").admitted)
            self.assertFalse(transport.publish("output", b"Msg", b"c").admitted)
            self.assertTrue(transport.online)
            self.assertTrue(transport.publish("output", b"BusPulse", b"c").admitted)
            self.assertFalse(
                transport.publish("output", b"BusPulse", b"d", handshake=True).admitted
            )
            self.assertTrue(transport.online)
            self.assertEqual(self.failures, [])

    def test_writer_settles_the_batch_even_when_telemetry_fails(self):
        """A metrics raise must not strand a published batch unsettled."""
        transport = self._transport()
        self.assertTrue(transport.publish("output", b"Msg", b"x").admitted)
        with patch.object(transport_module, "prometheus_metrics", new=_RaisingMetrics()):
            transport._write_batch(transport._take_write_batch())
        self._run_callbacks()
        self.assertEqual(transport.outgoing_count, 0)
        self.assertEqual(transport.outgoing_bytes, 0)
        self.assertEqual(self.failures, [])

    def test_reject_path_survives_broken_telemetry(self):
        """The rejection result outranks telemetry bookkeeping."""
        transport = self._transport()
        with (
            patch.multiple(transport_module, DATA_ENTRIES=1, DATA_BYTES=10_000),
            patch.object(transport_module, "prometheus_metrics", new=_RaisingMetrics()),
        ):
            self.assertTrue(transport.publish("output", b"Msg", b"a").admitted)
            result = transport.publish("output", b"Msg", b"b")
        self.assertFalse(result.admitted)
        self.assertTrue(transport.online)

    def test_write_batch_counts_published_frames_in_one_call(self):
        """Published frames settle the counter once, with the batch count."""
        transport = self._transport()
        for _ in range(3):
            self.assertTrue(transport.publish("output", b"Msg", b"x").admitted)
        batch = transport._take_write_batch()
        with patch.object(transport_module, "prometheus_metrics") as metrics:
            transport._write_batch(batch)
        metrics.record_bus_publish.assert_called_once_with(3)

    def test_per_command_xadd_error_settles_frames_and_fails_transport(self):
        """A response entry that is an error is a lost publication: escalate."""
        transport = self._transport()
        self.assertTrue(transport.publish("output", b"Msg", b"bad").admitted)
        self.assertTrue(transport.publish("output", b"Msg", b"good").admitted)
        transport._client.pipeline = lambda transaction=False: _PartialFailurePipeline()
        transport._write_batch(transport._take_write_batch())
        self._run_callbacks()
        self.assertEqual(transport.outgoing_count, 0)
        self.assertFalse(transport.online)
        self.assertEqual(len(self.failures), 1)
