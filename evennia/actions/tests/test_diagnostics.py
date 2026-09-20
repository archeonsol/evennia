"""Dispatch diagnostics: slow suspension and slow bridge logging."""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from evennia.actions.dispatch import _note_slow_dispatch
from evennia.actions.engine import RuleEngine


class SuspensionWarnTests(SimpleTestCase):
    """ACTION_SUSPENSION_WARN_MS names the rule that holds a dispatch open."""

    @staticmethod
    def _provider():
        return type("Provider", (), {})()

    @override_settings(ACTION_SUSPENSION_WARN_MS=10.0)
    def test_slow_suspension_logs_rule_and_phase(self):
        spec = SimpleNamespace(rule_name="carry_out_probe")

        with mock.patch("evennia.utils.logger.log_warn") as warn:
            RuleEngine._note_suspension(self._provider(), spec, "carry_out", 0.05, "deferred")

        self.assertTrue(warn.called)
        message = warn.call_args[0][0]
        self.assertIn("carry_out_probe", message)
        self.assertIn("phase=carry_out", message)
        self.assertIn("50.0ms", message)

    @override_settings(ACTION_SUSPENSION_WARN_MS=10.0)
    def test_fast_suspension_is_silent(self):
        spec = SimpleNamespace(rule_name="carry_out_probe")

        with mock.patch("evennia.utils.logger.log_warn") as warn:
            RuleEngine._note_suspension(self._provider(), spec, "carry_out", 0.001, "deferred")

        self.assertFalse(warn.called)

    @override_settings(ACTION_SUSPENSION_WARN_MS=0.0)
    def test_disabled_by_default(self):
        spec = SimpleNamespace(rule_name="carry_out_probe")

        with mock.patch("evennia.utils.logger.log_warn") as warn:
            RuleEngine._note_suspension(self._provider(), spec, "carry_out", 5.0, "generator")

        self.assertFalse(warn.called)


class DispatchWarnTests(SimpleTestCase):
    """ACTION_DISPATCH_WARN_MS splits the bridge span the marker measures."""

    @override_settings(ACTION_DISPATCH_WARN_MS=10.0)
    def test_slow_dispatch_logs_parse_and_dispatch_split(self):
        with mock.patch("evennia.utils.logger.log_warn") as warn:
            _note_slow_dispatch("look", 0.0, 0.005, 0.050)

        self.assertTrue(warn.called)
        message = warn.call_args[0][0]
        self.assertIn("parse=5.0ms", message)
        self.assertIn("dispatch=45.0ms", message)
        self.assertIn("total=50.0ms", message)

    @override_settings(ACTION_DISPATCH_WARN_MS=10.0)
    def test_fast_dispatch_is_silent(self):
        with mock.patch("evennia.utils.logger.log_warn") as warn:
            _note_slow_dispatch("look", 0.0, 0.001, 0.002)

        self.assertFalse(warn.called)

    @override_settings(ACTION_DISPATCH_WARN_MS=0.0)
    def test_disabled_by_default(self):
        with mock.patch("evennia.utils.logger.log_warn") as warn:
            _note_slow_dispatch("look", 0.0, 0.0, 5.0)

        self.assertFalse(warn.called)
