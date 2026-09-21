"""Tests for the best-effort telemetry seam and label closed sets."""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from evennia.server import bus_result, prometheus_metrics, redis_transport
from evennia.server.bus_result import BUS_REJECT_REASONS


class TestBestEffort(SimpleTestCase):
    def test_forwards_positional_and_keyword_arguments(self):
        calls = []
        prometheus_metrics.best_effort("probe", lambda *a, **k: calls.append((a, k)), 1, x=2)
        self.assertEqual(calls, [((1,), {"x": 2})])

    def test_raising_recorder_logs_and_continues(self):
        def boom():
            raise RuntimeError("broken recorder")

        with patch("evennia.utils.logger.log_warn") as warn:
            prometheus_metrics.best_effort("probe", boom)
        warn.assert_called_once()
        self.assertIn("probe", warn.call_args.args[0])


class TestBusRejectLabels(SimpleTestCase):
    """Reject labels are the shared closed set, never truncated variants."""

    def _counter(self):
        counter = Mock()
        init = patch.object(prometheus_metrics, "_init_metrics", return_value=True)
        metric = patch.object(prometheus_metrics, "BUS_REJECTED_TOTAL", counter)
        init.start()
        metric.start()
        self.addCleanup(init.stop)
        self.addCleanup(metric.stop)
        return counter

    def test_known_reasons_are_not_truncated(self):
        counter = self._counter()
        for reason in sorted(BUS_REJECT_REASONS):
            prometheus_metrics.record_bus_reject(reason)
            counter.labels.assert_called_with(reason=reason)

    def test_unknown_reason_falls_back_to_one_label(self):
        counter = self._counter()
        prometheus_metrics.record_bus_reject("some operator-typed prose")
        counter.labels.assert_called_with(reason="unknown")

    def test_fence_comparison_uses_the_shared_constant(self):
        self.assertIs(redis_transport.CAPACITY_EXHAUSTED, bus_result.CAPACITY_EXHAUSTED)


class TestClosedSetFallbacks(SimpleTestCase):
    """Out-of-set values normalize to one bounded label, never new series."""

    def test_flush_source_falls_back_to_manual(self):
        with (
            patch.object(prometheus_metrics, "_init_metrics", return_value=True),
            patch.object(prometheus_metrics, "ATTR_FLUSH_RUNS_TOTAL") as runs,
            patch.object(prometheus_metrics, "ATTR_FLUSH_TOTAL"),
            patch.object(prometheus_metrics, "ATTR_FLUSH_BACKENDS_TOTAL"),
            patch.object(prometheus_metrics, "ATTR_DIRTY_PENDING"),
            patch.object(prometheus_metrics, "ATTR_FLUSH_DURATION_SECONDS") as duration,
        ):
            prometheus_metrics.record_attribute_flush(
                {"total": 1, "backends": 1, "pending": 0}, duration_seconds=0.1, source="bogus"
            )
        runs.labels.assert_called_with(source="manual")
        duration.labels.assert_called_with(source="manual")

    def test_prewarm_outcome_falls_back_to_failed(self):
        with (
            patch.object(prometheus_metrics, "_init_metrics", return_value=True),
            patch.object(prometheus_metrics, "AUTHORIZATION_PREWARM_TOTAL") as total,
            patch.object(prometheus_metrics, "AUTHORIZATION_PREWARM_DURATION_SECONDS"),
        ):
            prometheus_metrics.record_authorization_prewarm("bogus", 0.1)
        total.labels.assert_called_with(outcome="failed")

    def test_prewarm_wait_outcome_falls_back_to_failed(self):
        with (
            patch.object(prometheus_metrics, "_init_metrics", return_value=True),
            patch.object(prometheus_metrics, "AUTHORIZATION_PREWARM_WAIT_TOTAL") as total,
            patch.object(prometheus_metrics, "AUTHORIZATION_PREWARM_WAIT_DURATION_SECONDS"),
        ):
            prometheus_metrics.record_authorization_prewarm_wait("bogus", 0.1)
        total.labels.assert_called_with(outcome="failed")

    def test_render_phase_falls_back_to_transform(self):
        samples = {"resolve": 0, "transform": 0, "clean": 0}
        with (
            patch.object(prometheus_metrics, "_render_phase_samples", samples),
            patch.object(prometheus_metrics, "_init_metrics", return_value=False),
        ):
            prometheus_metrics.record_render_phase("bogus", 0.1)
        self.assertEqual(samples["transform"], 1)
        self.assertNotIn("bogus", samples)


class TestCallSitesLogInsteadOfPass(SimpleTestCase):
    def test_clean_duration_records_after_recorder_failure(self):
        from evennia.server.sessionhandler import ServerSessionHandler

        with (
            patch.object(
                prometheus_metrics, "record_render_phase", side_effect=RuntimeError("boom")
            ),
            patch("evennia.utils.logger.log_warn") as warn,
        ):
            ServerSessionHandler._record_clean_duration(0.0)
        warn.assert_called_once()

    def test_prewarm_wait_returns_value_after_recorder_failure(self):
        from evennia.authorization.storage import _finish_prewarm_wait

        with (
            patch.object(
                prometheus_metrics,
                "record_authorization_prewarm_wait",
                side_effect=RuntimeError("boom"),
            ),
            patch("evennia.utils.logger.log_warn") as warn,
        ):
            result = _finish_prewarm_wait(True, "ready", 0.0)
        self.assertTrue(result)
        warn.assert_called_once()
