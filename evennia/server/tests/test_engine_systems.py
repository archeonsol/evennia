"""Failure escalation for scheduled attribute persistence."""

from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase

from evennia.server import engine_systems


class TestFlushFailureEscalation(SimpleTestCase):
    """Exercise returned failures and exceptions without database access."""

    def setUp(self):
        """Isolate counters, the flush producer, metrics, and logging."""
        super().setUp()
        counters = patch.multiple(
            engine_systems, _consecutive_flush_failures=0, _flush_fire_count=0
        )
        counters.start()
        self.addCleanup(counters.stop)
        self.flush = self._patch("_flush_all_dirty")
        self.metrics = self._patch("maybe_log_flush_metrics")
        self.backlog = self._patch("maybe_warn_pending_dirty")
        self.logger = self._patch("logger")

    def _patch(self, name):
        """Patch a module seam and restore it on cleanup."""
        patcher = patch.object(engine_systems, name)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def _stats(self, *, failed=0, persisted=0, spooled=0):
        """Build the documented producer result for a batch."""
        count = failed + persisted + spooled
        return {
            "backends": count,
            "total": persisted + spooled,
            "pending": count,
            "failed": failed,
            "spooled": spooled,
            "oldest_age": 0,
        }

    def test_failed_and_mixed_batches_count_once(self):
        """One fire fails even when other rows persist or reach the spool."""
        for stats in (
            self._stats(failed=4),
            self._stats(failed=2, persisted=3, spooled=1),
        ):
            with self.subTest(stats=stats):
                engine_systems._consecutive_flush_failures = 0
                self.flush.return_value = stats
                self.logger.reset_mock()
                engine_systems._run_flush(None)
                self.assertEqual(engine_systems._consecutive_flush_failures, 1)
                self.logger.log_err.assert_called_once()
                self.assertIn(
                    f"{stats['failed']} undurable rows", self.logger.log_err.call_args.args[0]
                )
                self.logger.log_trace.assert_not_called()

    def test_returned_failures_and_exceptions_share_streak(self):
        """An intervening exception does not restart the returned-failure streak."""
        self.flush.side_effect = [
            self._stats(failed=1),
            RuntimeError("down"),
            self._stats(failed=1),
        ]
        for _ in range(3):
            engine_systems._run_flush(None)
        self.assertEqual(engine_systems._consecutive_flush_failures, 3)
        self.logger.log_trace.assert_called_once()
        criticals = [
            c.args[0]
            for c in self.logger.log_err.call_args_list
            if c.args[0].startswith("CRITICAL:")
        ]
        self.assertEqual(len(criticals), 1)
        self.assertIn("3 consecutive fires", criticals[0])

    def test_critical_repeats_at_existing_cadence(self):
        """Persistent returned failures alert on fires 3, 13, and 23 only."""
        self.flush.return_value = self._stats(failed=1)
        critical_fires = []
        for fire in range(1, 26):
            self.logger.reset_mock()
            engine_systems._run_flush(None)
            errors = [c.args[0] for c in self.logger.log_err.call_args_list]
            self.assertEqual(sum(not line.startswith("CRITICAL:") for line in errors), 1)
            if any(line.startswith("CRITICAL:") for line in errors):
                critical_fires.append(fire)
        self.assertEqual(critical_fires, [3, 13, 23])

    def test_durable_and_empty_batches_reset_streak(self):
        """Persisted, spooled, and empty batches all break a failure streak."""
        for success in (self._stats(persisted=2), self._stats(spooled=2), self._stats()):
            with self.subTest(success=success):
                engine_systems._consecutive_flush_failures = 2
                self.flush.return_value = success
                self.logger.reset_mock()
                engine_systems._run_flush(None)
                self.assertEqual(engine_systems._consecutive_flush_failures, 0)
                self.logger.log_err.assert_not_called()
                self.logger.log_trace.assert_not_called()
                self.flush.return_value = self._stats(failed=1)
                engine_systems._run_flush(None)
                self.assertEqual(engine_systems._consecutive_flush_failures, 1)
                self.logger.log_err.assert_called_once()

    def test_metrics_receive_every_returned_batch_in_order(self):
        """Success and failure retain their stats and monotonically advancing fire count."""
        observed = Mock()
        observed.attach_mock(self.metrics, "metrics")
        observed.attach_mock(self.backlog, "backlog")
        batches = [self._stats(persisted=1), self._stats(failed=1)]
        self.flush.side_effect = batches
        for _ in batches:
            engine_systems._run_flush(None)
        self.assertEqual(
            observed.mock_calls,
            [
                call.metrics(batches[0], 1),
                call.backlog(batches[0], 1),
                call.metrics(batches[1], 2),
                call.backlog(batches[1], 2),
            ],
        )
        self.assertEqual(engine_systems._flush_fire_count, 2)

    def test_metrics_exceptions_remain_visible_failures(self):
        """An exception in either monitoring helper still counts once and traces."""
        for helper in (self.metrics, self.backlog):
            with self.subTest(helper=helper):
                engine_systems._consecutive_flush_failures = 2
                self.flush.return_value = self._stats(persisted=1)
                self.logger.reset_mock()
                helper.side_effect = RuntimeError("monitoring failure")
                engine_systems._run_flush(None)
                helper.side_effect = None
                self.assertEqual(engine_systems._consecutive_flush_failures, 3)
                self.logger.log_trace.assert_called_once()
                self.assertEqual(self.logger.log_err.call_count, 2)
