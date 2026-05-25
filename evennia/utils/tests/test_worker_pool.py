"""
Tests for worker_pool.defer_to_worker.
"""

from django.test import override_settings

from evennia.utils.test_resources import BaseEvenniaTest
from evennia.utils.worker_pool import defer_to_worker


class TestWorkerPool(BaseEvenniaTest):
    @override_settings(ENGINE_WORKER_POOL_ENABLED=True)
    def test_defer_to_worker_runs_in_thread(self):
        import threading as th

        seen = []
        done = th.Event()

        def work():
            seen.append(th.current_thread().name)
            return 42

        def check(result):
            self.assertEqual(result, 42)
            self.assertTrue(seen)
            self.assertNotEqual(seen[0], th.main_thread().name)
            done.set()

        defer_to_worker(work).addCallback(check)
        self.assertTrue(done.wait(timeout=5.0))
