"""Batched writer and capacity backpressure for the Redis transport.

These run without a real Redis: a fake client records the pipeline the writer
builds, and the captured event-loop callbacks are executed by hand. The live
fault matrix stays in ``test_redis_transport_live.py``.
"""

import queue
from unittest.mock import patch

from django.test import SimpleTestCase

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


class WriterBatchTest(SimpleTestCase):
    """One Redis round trip per batch, in wire order, capacity as backpressure."""

    def setUp(self):
        self.callbacks = queue.Queue()
        self.failures = []
        self.enterContext(
            patch.object(
                transport_module.clock,
                "call_from_thread",
                side_effect=lambda fn, *args: self.callbacks.put((fn, args)),
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
