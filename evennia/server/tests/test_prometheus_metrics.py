"""Tests for the best-effort telemetry seam."""

from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.server import prometheus_metrics


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
