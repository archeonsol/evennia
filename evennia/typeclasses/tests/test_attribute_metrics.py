"""
Tests for the attribute write-behind flush observability surface.

Covers:
  - flush_all_dirty forwards stats to the Prometheus primitive
  - metrics errors are *not* silently swallowed (no-silent-errors contract)
  - maybe_warn_pending_dirty backlog warning honors its threshold setting
"""

from unittest import TestCase
from unittest.mock import patch

from django.test import override_settings

from evennia.server import prometheus_metrics
from evennia.typeclasses import attribute_metrics, attributes, jsonb_handler
from evennia.typeclasses.jsonb_handler import FlushResult


class _FakeState:
    """Stand-in dirty JSONB row state for seam tests."""

    key = ("default", "typeclass", 7)

    def dirty_age(self):
        return 0.0


class TestFlushMetrics(TestCase):
    def setUp(self):
        attributes.discard_dirty_backends()
        self.state = _FakeState()

    def tearDown(self):
        attributes.discard_dirty_backends()

    def _seams(self, persist_return=None):
        return (
            patch.object(jsonb_handler, "dirty_jsonb_row_states", return_value=[self.state]),
            patch.object(
                jsonb_handler,
                "_persist_row_state",
                return_value=persist_return or FlushResult(ok=True),
            ),
        )

    def test_legacy_dirty_backend_registry_is_gone(self):
        self.assertFalse(hasattr(attributes, "_DIRTY_BACKENDS"))

    def test_flush_records_metrics(self):
        states, persist = self._seams()
        with states, persist, patch.object(prometheus_metrics, "record_attribute_flush") as rec:
            stats = attributes.flush_all_dirty()

        self.assertEqual(stats["total"], 1)
        rec.assert_called_once()
        recorded_stats, kwargs = rec.call_args.args[0], rec.call_args.kwargs
        self.assertEqual(recorded_stats["total"], 1)
        self.assertIn("duration_seconds", kwargs)
        self.assertEqual(kwargs["source"], "manual")

    def test_flush_forwards_bounded_source(self):
        states, persist = self._seams()
        with states, persist, patch.object(prometheus_metrics, "record_attribute_flush") as rec:
            attributes.flush_all_dirty(source="barrier")

        self.assertEqual(rec.call_args.kwargs["source"], "barrier")

    def test_metrics_error_is_not_swallowed(self):
        # Deliberate contract: a genuine metrics bug surfaces rather than vanishing.
        # The structural guards in record_attribute_flush handle the only expected
        # condition (prometheus absent); anything else is a real error.
        states, persist = self._seams()
        with (
            states,
            persist as persist_row,
            patch.object(
                prometheus_metrics, "record_attribute_flush", side_effect=RuntimeError("boom")
            ),
        ):
            with self.assertRaises(RuntimeError):
                attributes.flush_all_dirty()
        # The actual flush still completed before the metrics call.
        persist_row.assert_called_once()


class TestWarnPendingDirty(TestCase):
    @override_settings(ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD=10)
    def test_warns_at_or_above_threshold(self):
        with patch.object(attribute_metrics.logger, "log_warn") as warn:
            attribute_metrics.maybe_warn_pending_dirty({"pending": 10}, tick_count=1)
        warn.assert_called_once()

    @override_settings(ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD=10)
    def test_silent_below_threshold(self):
        with patch.object(attribute_metrics.logger, "log_warn") as warn:
            attribute_metrics.maybe_warn_pending_dirty({"pending": 9}, tick_count=1)
        warn.assert_not_called()

    @override_settings(ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD=0)
    def test_disabled_when_threshold_zero(self):
        with patch.object(attribute_metrics.logger, "log_warn") as warn:
            attribute_metrics.maybe_warn_pending_dirty({"pending": 9999}, tick_count=1)
        warn.assert_not_called()
