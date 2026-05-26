"""
Tests for worker_pool.defer_to_worker.

These exercise the wrapper without driving the Twisted reactor.
``deferToThread`` schedules its callback via ``reactor.callFromThread``,
which never fires inside a Django ``TestCase`` (no running reactor),
so we patch ``threads.deferToThread`` and assert the wrapper's contract
directly: the wrapped callable closes over the args, callback/errback
get chained, the disabled-pool path raises, and the elapsed-time
warning fires past the configured threshold.
"""

from unittest.mock import patch

from django.test import override_settings
from twisted.internet.defer import Deferred

from evennia.utils import worker_pool
from evennia.utils.test_resources import BaseEvenniaTest


class TestWorkerPool(BaseEvenniaTest):
    @override_settings(ENGINE_WORKER_POOL_ENABLED=True)
    def test_defer_to_worker_returns_deferred_and_wraps_call(self):
        captured = {}

        def fake_defer_to_thread(fn, *a, **kw):
            captured["fn"] = fn
            captured["result"] = fn()
            return Deferred()

        with patch.object(worker_pool.threads, "deferToThread", fake_defer_to_thread):
            d = worker_pool.defer_to_worker(lambda x, y: x + y, 2, y=3)

        self.assertIsInstance(d, Deferred)
        self.assertEqual(captured["result"], 5)

    @override_settings(ENGINE_WORKER_POOL_ENABLED=True)
    def test_defer_to_worker_chains_callback_and_errback(self):
        cb_seen = []
        eb_seen = []

        def fake_defer_to_thread(fn, *a, **kw):
            return Deferred()

        with patch.object(worker_pool.threads, "deferToThread", fake_defer_to_thread):
            d = worker_pool.defer_to_worker(
                lambda: 42, callback=cb_seen.append, errback=eb_seen.append
            )

        d.callback("hit")
        self.assertEqual(cb_seen, ["hit"])
        self.assertEqual(eb_seen, [])

    @override_settings(ENGINE_WORKER_POOL_ENABLED=False)
    def test_defer_to_worker_raises_when_disabled(self):
        with self.assertRaises(RuntimeError):
            worker_pool.defer_to_worker(lambda: None)

    @override_settings(ENGINE_WORKER_POOL_ENABLED=True, ENGINE_WORKER_BLOCK_WARN_MS=0.0001)
    def test_defer_to_worker_logs_when_elapsed_exceeds_threshold(self):
        # Force the cached threshold to re-read settings.
        worker_pool._warn_ms = None

        def fake_defer_to_thread(fn, *a, **kw):
            fn()
            return Deferred()

        with patch.object(worker_pool.threads, "deferToThread", fake_defer_to_thread):
            d = worker_pool.defer_to_worker(lambda: "ok")

        with patch("evennia.utils.logger.log_warn") as mock_warn:
            d.callback("ok")
            # The elapsed check sits on addBoth; trip it by letting any delay
            # past 0.0001ms * 10 register, which is effectively always.
            self.assertTrue(mock_warn.called)
        worker_pool._warn_ms = None
