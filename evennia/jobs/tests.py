"""
Tests for whitelisted job queue.
"""

import sys
import types
from unittest import mock

from django.test import override_settings

from evennia.jobs import queue
from evennia.jobs.queue import enqueue_job, process_pending_jobs, register_job_type
from evennia.utils.test_resources import BaseEvenniaTest

# A Redis-shaped ConnectionError built without the optional ``redis`` dependency:
# matched structurally by module/name, exactly like the real redis exception.
_RedisConnError = type("ConnectionError", (Exception,), {"__module__": "redis.exceptions"})


def _sample_job(payload):
    _sample_job.last_payload = payload


_sample_job.last_payload = None


class TestJobQueue(BaseEvenniaTest):
    @override_settings(
        JOB_QUEUE_ENABLED=True,
        JOB_QUEUE_BACKEND="postgres",
        JOB_QUEUE_REGISTRY={"sample": "evennia.jobs.tests._sample_job"},
    )
    def test_enqueue_and_process(self):
        register_job_type("sample", "evennia.jobs.tests._sample_job")
        job_id = enqueue_job("sample", {"n": 3})
        self.assertTrue(job_id)
        n = process_pending_jobs(max_jobs=5)
        self.assertEqual(n, 1)
        self.assertEqual(_sample_job.last_payload, {"n": 3})

    def test_rejects_unknown_job_type(self):
        self.assertIsNone(enqueue_job("not_registered", {}))


class TestRedisBackoff(BaseEvenniaTest):
    """Redis availability is logged calmly: complain once until live, then probe."""

    def setUp(self):
        super().setUp()
        # Isolate module-level availability state between tests.
        queue._redis_seen_alive = False
        queue._redis_down_count = 0
        queue._redis_last_down_log = 0.0

    def _fake_redis(self):
        """Return (module, conn); set conn.rpop/conn.ping side effects per test."""
        conn = mock.Mock()
        module = types.SimpleNamespace(get_redis_connection=lambda alias=None: conn)
        return module, conn

    def test_connection_error_logs_once_no_traceback(self):
        module, conn = self._fake_redis()
        conn.rpop.side_effect = _RedisConnError("connection refused")
        with (
            mock.patch.dict(sys.modules, {"django_redis": module}),
            mock.patch.object(queue.logger, "log_trace") as mock_trace,
            mock.patch.object(queue.logger, "log_warn") as mock_warn,
        ):
            for _ in range(20):
                self.assertIsNone(queue._dequeue_redis())

        mock_trace.assert_not_called()
        self.assertEqual(mock_warn.call_count, 1)  # never seen alive -> complain once
        self.assertEqual(queue._redis_down_count, 20)

    def test_not_installed_logs_once_no_traceback(self):
        # The optional redis dependency missing entirely (the default backend is
        # "redis"): a polled dequeue must not raise a traceback every tick.
        with (
            mock.patch.dict(sys.modules, {"django_redis": None}),
            mock.patch.object(queue.logger, "log_trace") as mock_trace,
            mock.patch.object(queue.logger, "log_warn") as mock_warn,
        ):
            for _ in range(20):
                self.assertIsNone(queue._dequeue_redis())

        mock_trace.assert_not_called()
        self.assertEqual(mock_warn.call_count, 1)

    def test_unexpected_error_still_logs_traceback(self):
        module, conn = self._fake_redis()
        conn.rpop.side_effect = ValueError("boom")
        with (
            mock.patch.dict(sys.modules, {"django_redis": module}),
            mock.patch.object(queue.logger, "log_trace") as mock_trace,
            mock.patch.object(queue.logger, "log_warn") as mock_warn,
        ):
            self.assertIsNone(queue._dequeue_redis())

        mock_trace.assert_called_once()
        mock_warn.assert_not_called()

    @override_settings(JOB_QUEUE_ENABLED=True, JOB_QUEUE_BACKEND="redis")
    def test_boot_check_live_logs_once(self):
        module, conn = self._fake_redis()  # conn.ping() returns a Mock (no error)
        with (
            mock.patch.dict(sys.modules, {"django_redis": module}),
            mock.patch.object(queue.logger, "log_info") as mock_info,
        ):
            self.assertTrue(queue.check_redis_backend())
            self.assertTrue(queue.check_redis_backend())  # second probe is quiet

        self.assertTrue(queue._redis_seen_alive)
        mock_info.assert_called_once()  # "is live" logged once

    @override_settings(JOB_QUEUE_ENABLED=True, JOB_QUEUE_BACKEND="redis")
    def test_outage_after_live_rechecks_and_heartbeats(self):
        module, conn = self._fake_redis()
        with (
            mock.patch.dict(sys.modules, {"django_redis": module}),
            mock.patch.object(queue.logger, "log_warn") as mock_warn,
            mock.patch.object(queue.logger, "log_info") as mock_info,
            mock.patch.object(queue, "time") as mock_time,
        ):
            # Boot: Redis is live.
            mock_time.monotonic.return_value = 0.0
            self.assertTrue(queue.check_redis_backend())
            self.assertTrue(queue._redis_seen_alive)

            # Redis drops. Re-probe each tick; "still down" logs at first loss and
            # again only once the heartbeat window (600s) has elapsed.
            conn.ping.side_effect = _RedisConnError("down")
            for t in (60.0, 120.0, 300.0, 660.0, 720.0):
                mock_time.monotonic.return_value = t
                self.assertFalse(queue.check_redis_backend())
            self.assertEqual(mock_warn.call_count, 2)  # at t=60 and t=660
            self.assertEqual(queue._redis_down_count, 5)

            # Redis returns: the next probe recovers and logs once.
            conn.ping.side_effect = None
            mock_time.monotonic.return_value = 800.0
            self.assertTrue(queue.check_redis_backend())

        self.assertEqual(mock_info.call_count, 2)  # "is live" + "recovered"
        self.assertEqual(queue._redis_down_count, 0)

    @override_settings(JOB_QUEUE_ENABLED=True, JOB_QUEUE_BACKEND="postgres")
    def test_check_skips_non_redis_backend(self):
        with mock.patch.object(queue, "_redis_ping") as mock_ping:
            self.assertTrue(queue.check_redis_backend())
        mock_ping.assert_not_called()

    @override_settings(JOB_QUEUE_ENABLED=True, JOB_QUEUE_BACKEND="redis")
    def test_enqueue_returns_none_while_down_and_id_after_recovery(self):
        # A dropped job must not be reported as queued: enqueue_job returns
        # None during an outage and a real id again once Redis answers.
        register_job_type("backoff_sample", "evennia.jobs.tests._sample_job")
        module, conn = self._fake_redis()
        conn.lpush.side_effect = _RedisConnError("connection refused")
        with (
            mock.patch.dict(sys.modules, {"django_redis": module}),
            mock.patch.object(queue.logger, "log_warn") as mock_warn,
        ):
            for _ in range(5):
                self.assertIsNone(enqueue_job("backoff_sample", {"n": 1}))
            self.assertEqual(mock_warn.call_count, 1)  # complain once, not per drop

            conn.lpush.side_effect = None
            self.assertIsNotNone(enqueue_job("backoff_sample", {"n": 2}))
