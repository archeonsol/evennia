"""
Tests for command_trace context.
"""

from django.test import override_settings

from evennia.utils.command_trace import (
    begin_command_trace,
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
