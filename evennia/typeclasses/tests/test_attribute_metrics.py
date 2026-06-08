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
from evennia.typeclasses import attribute_metrics, attributes


class _FakeBackend:
    """Stand-in dirty backend: reports a fixed pending count, flush is a no-op."""

    def __init__(self, pending):
        self._pending = pending
        self.flushed = False

    def pending_count(self):
        return self._pending

    def flush_dirty(self):
        self.flushed = True


class TestFlushMetrics(TestCase):
    def setUp(self):
        attributes.discard_dirty_backends()
        self.backend = _FakeBackend(3)
        attributes._DIRTY_BACKENDS.add(self.backend)

    def tearDown(self):
        attributes.discard_dirty_backends()

    def test_flush_records_metrics(self):
        with patch.object(prometheus_metrics, "record_attribute_flush") as rec:
            stats = attributes.flush_all_dirty()

        self.assertTrue(self.backend.flushed)
        self.assertEqual(stats["total"], 3)
        rec.assert_called_once()
        recorded_stats, kwargs = rec.call_args.args[0], rec.call_args.kwargs
        self.assertEqual(recorded_stats["total"], 3)
        self.assertIn("duration_seconds", kwargs)

    def test_metrics_error_is_not_swallowed(self):
        # Deliberate contract: a genuine metrics bug surfaces rather than vanishing.
        # The structural guards in record_attribute_flush handle the only expected
        # condition (prometheus absent); anything else is a real error.
        with patch.object(
            prometheus_metrics, "record_attribute_flush", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                attributes.flush_all_dirty()
        # The actual flush still completed before the metrics call.
        self.assertTrue(self.backend.flushed)


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
