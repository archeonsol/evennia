"""
Tests for the command instrumentation seam (trace context, metrics, markers).
"""

from unittest.mock import patch

from django.test import override_settings

from evennia.utils.command_trace import (
    begin_command_trace,
    command_trace_scope,
    end_command_trace,
    get_trace_id,
    trace_log_context,
)
from evennia.utils.test_resources import BaseEvenniaTest


class TestCommandTrace(BaseEvenniaTest):
    @override_settings(COMMAND_TRACE_ENABLED=True)
    def test_trace_lifecycle(self):
        tid = begin_command_trace(caller=self.char1, raw_string="say hi", cmd_key="say")
        self.assertEqual(len(tid), 16)
        self.assertEqual(get_trace_id(), tid)
        ctx = trace_log_context()
        self.assertEqual(ctx.get("trace_id"), tid)
        self.assertEqual(ctx.get("cmd_key"), "say")
        end_command_trace()
        self.assertIsNone(get_trace_id())


class _CapturingSession:
    """Capture raw records sent to one input session."""

    def __init__(self):
        self.messages = []

    def msg(self, text=None, **kwargs):
        self.messages.append((text, kwargs))


class TestCommandTraceScope(BaseEvenniaTest):
    """One seam owns the trace lifecycle, the outcome metric, and markers."""

    def test_scope_traces_inside_and_clears_after(self):
        with command_trace_scope(caller=self.char1, raw_string="say hi", cmd_key="say"):
            self.assertIsNotNone(get_trace_id())
        self.assertIsNone(get_trace_id())

    def test_scope_clears_trace_after_body_raise(self):
        with self.assertRaises(RuntimeError):
            with command_trace_scope(cmd_key="say"):
                raise RuntimeError("boom")
        self.assertIsNone(get_trace_id())

    def test_scope_records_metric_with_body_outcome(self):
        with patch("evennia.server.prometheus_metrics.record_action_input") as record:
            with command_trace_scope(cmd_key="look", metrics=True) as scope:
                scope.outcome = "consumed"
        self.assertEqual(record.call_args[0][0], "consumed")
        self.assertGreaterEqual(record.call_args[0][1], 0.0)

    def test_scope_records_error_metric_on_raise(self):
        with patch("evennia.server.prometheus_metrics.record_action_input") as record:
            with self.assertRaises(RuntimeError):
                with command_trace_scope(cmd_key="look", metrics=True):
                    raise RuntimeError("boom")
        self.assertEqual(record.call_args[0][0], "error")

    def test_scope_skips_metric_when_disabled(self):
        with patch("evennia.server.prometheus_metrics.record_action_input") as record:
            with command_trace_scope(cmd_key="look"):
                pass
        record.assert_not_called()

    @override_settings(COMMAND_COMPLETION_MARKERS_ENABLED=True)
    def test_scope_emits_raw_marker_after_body(self):
        session = _CapturingSession()
        with command_trace_scope(session=session, cmd_key="look", markers=True) as scope:
            scope.outcome = "succeeded"
        self.assertEqual(len(session.messages), 1)
        self.assertRegex(
            session.messages[0][0],
            r"^\x1eEV-COMMAND-DONE outcome=succeeded elapsed_ms=\d+\.\d{3}\x1f$",
        )
        self.assertEqual(session.messages[0][1]["options"], {"raw": True})

    @override_settings(COMMAND_COMPLETION_MARKERS_ENABLED=True)
    def test_scope_marker_uses_error_outcome_after_raise(self):
        session = _CapturingSession()
        with self.assertRaises(RuntimeError):
            with command_trace_scope(session=session, markers=True):
                raise RuntimeError("boom")
        self.assertIn("outcome=error", session.messages[0][0])

    @override_settings(COMMAND_COMPLETION_MARKERS_ENABLED=True)
    def test_scope_marker_skips_session_without_msg(self):
        with patch("evennia.utils.logger.log_trace") as log:
            with command_trace_scope(session=object(), markers=True):
                pass
        log.assert_not_called()

    def test_begin_survives_raising_identity_properties(self):
        class Unruly:
            """A caller whose game-defined property raises."""

            @property
            def key(self):
                raise RuntimeError("property kaboom")

        tid = begin_command_trace(caller=Unruly(), raw_string="x", cmd_key="x")
        self.assertEqual(len(tid), 16)
        end_command_trace()
