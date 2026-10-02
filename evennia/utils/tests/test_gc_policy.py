"""
Tests for evennia.utils.gc_policy.

Collector behavior is exercised against fakes: a clock the test advances, a loop
that records ``call_later``, and patched ``gc`` entry points. Nothing here runs a
real collection or leaves the interpreter's collector disabled.
"""

import gc
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from evennia.utils import gc_policy


class _Clock:
    """Manually advanced stand-in for ``time.perf_counter``."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _Handle:
    """Cancellable timer handle recorded by :class:`_Loop`."""

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Loop:
    """Event loop double that records scheduled callbacks instead of running them."""

    def __init__(self):
        self.scheduled = []

    def call_later(self, delay, callback):
        handle = _Handle()
        self.scheduled.append((delay, callback, handle))
        return handle

    def fire_last(self):
        """Run the most recently scheduled callback, as the loop would."""
        _delay, callback, _handle = self.scheduled[-1]
        callback()


class _PolicyTestCase(SimpleTestCase):
    """Isolate module state and make sure the real collector is never left disabled."""

    def setUp(self):
        super().setUp()
        self.clock = _Clock()
        patcher = patch.object(gc_policy, "_now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        gc_policy._reset_for_tests()
        self.addCleanup(gc_policy._reset_for_tests)
        self.addCleanup(self._restore_collector)
        self._was_enabled = gc.isenabled()

    def _restore_collector(self):
        if self._was_enabled and not gc.isenabled():
            gc.enable()

    def _collection(self, seconds, generation=2, collected=7):
        """Deliver one collection's start and stop notifications."""
        gc_policy._on_collection("start", {"generation": generation})
        self.clock.advance(seconds)
        gc_policy._on_collection("stop", {"generation": generation, "collected": collected})


class TestPauseTelemetry(_PolicyTestCase):
    def test_long_pause_is_remembered_with_its_generation(self):
        self._collection(0.12, generation=1, collected=5)

        ((start, end, generation, collected),) = gc_policy.recent_pauses()
        self.assertAlmostEqual(end - start, 0.12)
        self.assertEqual((generation, collected), (1, 5))

    def test_every_collection_reaches_metrics_but_only_long_ones_are_remembered(self):
        with patch.object(gc_policy.prometheus_metrics, "record_gc_pause") as record:
            self._collection(0.0005, generation=0)
            self._collection(0.2, generation=2)

        self.assertEqual(record.call_count, 2)
        generations = [pause[2] for pause in gc_policy.recent_pauses()]
        self.assertEqual(generations, [2])

    def test_stop_without_start_is_ignored(self):
        gc_policy._on_collection("stop", {"generation": 2, "collected": 1})

        self.assertEqual(gc_policy.recent_pauses(), [])

    def test_a_failing_recorder_never_escapes_the_collector_callback(self):
        with patch.object(
            gc_policy.prometheus_metrics, "record_gc_pause", side_effect=RuntimeError("boom")
        ):
            self._collection(0.2)

        self.assertEqual(len(gc_policy.recent_pauses()), 1)

    @override_settings(ENGINE_GC_PAUSE_WARN_MS=50)
    def test_pause_over_the_threshold_logs_one_warning(self):
        with patch.object(gc_policy.logger, "log_warn") as warn:
            self._collection(0.2, generation=2, collected=9)

        warn.assert_called_once()
        message = warn.call_args.args[0]
        self.assertIn("200ms", message)
        self.assertIn("generation 2", message)

    @override_settings(ENGINE_GC_PAUSE_WARN_MS=50)
    def test_pause_under_the_threshold_is_silent(self):
        with patch.object(gc_policy.logger, "log_warn") as warn:
            self._collection(0.01)

        warn.assert_not_called()

    @override_settings(ENGINE_GC_PAUSE_WARN_MS=0)
    def test_zero_threshold_disables_the_warning(self):
        with patch.object(gc_policy.logger, "log_warn") as warn:
            self._collection(5.0)

        warn.assert_not_called()

    @override_settings(ENGINE_GC_PAUSE_WARN_MS=50)
    def test_warnings_are_rate_limited_and_count_what_they_skipped(self):
        with patch.object(gc_policy.logger, "log_warn") as warn:
            self._collection(0.1)
            self.clock.advance(1.0)
            self._collection(0.1)
            self.assertEqual(warn.call_count, 1)

            self.clock.advance(gc_policy._WARN_EVERY_SECONDS)
            self._collection(0.1)

        self.assertEqual(warn.call_count, 2)
        self.assertIn("1 more", warn.call_args.args[0])


class TestIntentionalCollections(_PolicyTestCase):
    @override_settings(ENGINE_GC_PAUSE_WARN_MS=50)
    def test_a_deep_clean_reports_itself_instead_of_warning_about_itself(self):
        def slow_full_collection():
            gc_policy._on_collection("start", {"generation": 2})
            self.clock.advance(0.4)
            gc_policy._on_collection("stop", {"generation": 2, "collected": 11})
            return 11

        with (
            patch.object(gc_policy.gc, "collect", side_effect=slow_full_collection),
            patch.object(gc_policy.logger, "log_warn") as warn,
            patch.object(gc_policy.logger, "log_info") as info,
        ):
            result = gc_policy.deep_clean("test")

        warn.assert_not_called()
        info.assert_called_once()
        self.assertEqual(result.collected, 11)
        self.assertEqual(
            [pause[2] for pause in gc_policy.recent_pauses()],
            [2],
            "the pause is still remembered so the stall watchdog can name it",
        )

    @override_settings(ENGINE_GC_PAUSE_WARN_MS=50)
    def test_the_warning_returns_after_the_intentional_collection(self):
        with patch.object(gc_policy.gc, "collect", return_value=0):
            gc_policy.deep_clean("test")

        with patch.object(gc_policy.logger, "log_warn") as warn:
            self._collection(0.2)

        warn.assert_called_once()


class TestPauseOverlap(_PolicyTestCase):
    def setUp(self):
        super().setUp()
        gc_policy._RECENT_PAUSES.extend(
            [(10.0, 10.4, 2, 100), (20.0, 20.1, 1, 5), (30.0, 30.02, 1, 0)]
        )

    def test_overlap_sums_the_intersection_with_the_window(self):
        seconds, longest = gc_policy.pause_overlap(9.9, 10.2)

        self.assertAlmostEqual(seconds, 0.2)
        self.assertEqual(longest, (10.0, 10.4, 2, 100))

    def test_overlap_spans_several_pauses(self):
        seconds, longest = gc_policy.pause_overlap(0.0, 100.0)

        self.assertAlmostEqual(seconds, 0.52)
        self.assertEqual(longest[2], 2)

    def test_no_overlap_reports_nothing(self):
        self.assertEqual(gc_policy.pause_overlap(11.0, 19.0), (0.0, None))


class TestPolicyLifecycle(_PolicyTestCase):
    def setUp(self):
        super().setUp()
        self.loop = _Loop()
        self.gc = {
            name: self._patch_gc(name)
            for name in ("collect", "freeze", "disable", "enable", "get_count", "isenabled")
        }
        self.gc["get_count"].return_value = (0, 0, 0)
        self.gc["isenabled"].return_value = True
        bound = patch.object(gc_policy.clock, "get_bound_loop", return_value=self.loop)
        bound.start()
        self.addCleanup(bound.stop)

    def _patch_gc(self, name):
        patcher = patch.object(gc_policy.gc, name)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    @override_settings(ENGINE_GC_POLICY="default")
    def test_default_policy_leaves_the_collector_alone(self):
        status = gc_policy.apply_gc_policy()

        self.gc["disable"].assert_not_called()
        self.gc["collect"].assert_not_called()
        self.assertEqual(self.loop.scheduled, [])
        self.assertEqual(status.policy, "default")

    @override_settings(ENGINE_GC_POLICY="managed", ENGINE_GC_FREEZE_AT_START=True)
    def test_managed_policy_settles_the_boot_heap_then_takes_over(self):
        status = gc_policy.apply_gc_policy()

        self.gc["collect"].assert_called_once_with()
        self.gc["freeze"].assert_called_once_with()
        self.gc["disable"].assert_called_once_with()
        self.assertEqual(status.policy, "managed")
        self.assertEqual(len(self.loop.scheduled), 1)

    @override_settings(ENGINE_GC_POLICY="managed", ENGINE_GC_FREEZE_AT_START=False)
    def test_freezing_the_boot_heap_is_optional(self):
        gc_policy.apply_gc_policy()

        self.gc["freeze"].assert_not_called()

    @override_settings(
        ENGINE_GC_POLICY="managed", ENGINE_GC_INTERVAL_MS=40, ENGINE_GC_YOUNG_THRESHOLD=3000
    )
    def test_the_driver_collects_only_the_young_generation_past_the_threshold(self):
        gc_policy.apply_gc_policy()
        delay, _callback, _handle = self.loop.scheduled[-1]
        self.assertAlmostEqual(delay, 0.040)
        self.gc["collect"].reset_mock()

        self.gc["get_count"].return_value = (2999, 3, 1)
        self.loop.fire_last()
        self.gc["collect"].assert_not_called()

        self.gc["get_count"].return_value = (3000, 3, 1)
        self.loop.fire_last()
        self.gc["collect"].assert_called_once_with(0)

        self.assertEqual(len(self.loop.scheduled), 3, "each tick must re-arm the next one")

    @override_settings(ENGINE_GC_POLICY="managed")
    def test_a_failing_collection_does_not_stop_the_driver(self):
        gc_policy.apply_gc_policy()
        self.gc["collect"].reset_mock()
        self.gc["get_count"].return_value = (10**6, 0, 0)
        self.gc["collect"].side_effect = RuntimeError("boom")

        with patch.object(gc_policy.logger, "log_trace") as trace:
            self.loop.fire_last()

        trace.assert_called_once()
        self.assertEqual(len(self.loop.scheduled), 2)

    @override_settings(ENGINE_GC_POLICY="managed")
    def test_without_a_bound_loop_the_collector_stays_enabled(self):
        with (
            patch.object(gc_policy.clock, "get_bound_loop", return_value=None),
            patch.object(gc_policy.logger, "log_warn") as warn,
        ):
            status = gc_policy.apply_gc_policy()

        self.gc["disable"].assert_not_called()
        warn.assert_called_once()
        self.assertEqual(status.policy, "default")

    @override_settings(ENGINE_GC_POLICY="managed")
    def test_stopping_cancels_the_timer_and_restores_the_collector(self):
        gc_policy.apply_gc_policy()
        handle = self.loop.scheduled[-1][2]

        gc_policy.stop_gc_policy()

        self.assertTrue(handle.cancelled)
        self.gc["enable"].assert_called_once_with()
        self.loop.scheduled[-1][1]()  # a tick that raced the stop does nothing
        self.assertEqual(len(self.loop.scheduled), 1)

    @override_settings(ENGINE_GC_POLICY="managed")
    def test_applying_twice_replaces_the_driver_and_boots_only_once(self):
        gc_policy.apply_gc_policy()
        first = self.loop.scheduled[-1][2]

        gc_policy.apply_gc_policy()

        self.assertTrue(first.cancelled)
        self.assertEqual(self.gc["collect"].call_count, 1)
        self.assertEqual(self.gc["freeze"].call_count, 1)
        callbacks = [cb for cb in gc.callbacks if cb is gc_policy._on_collection]
        self.assertEqual(len(callbacks), 1)

    @override_settings(ENGINE_GC_POLICY="turbo")
    def test_an_unknown_policy_falls_back_to_default_with_a_warning(self):
        with patch.object(gc_policy.logger, "log_warn") as warn:
            status = gc_policy.apply_gc_policy()

        warn.assert_called_once()
        self.assertIn("turbo", warn.call_args.args[0])
        self.assertEqual(status.policy, "default")
        self.gc["disable"].assert_not_called()


class TestDeepClean(_PolicyTestCase):
    HOUR = 3600.0

    def setUp(self):
        super().setUp()
        self.collect = patch.object(gc_policy.gc, "collect", return_value=42)
        self.collect.start()
        self.addCleanup(self.collect.stop)
        self.sessions = 0
        gc_policy._state.managed = True
        gc_policy._state.last_deep_clean = 0.0

    def _due(self, now):
        return gc_policy.deep_clean_if_due(now=now, session_count=lambda: self.sessions)

    @override_settings(ENGINE_GC_DEEP_CLEAN_INTERVAL=86400, ENGINE_GC_DEEP_CLEAN_MAX_DEFER=259200)
    def test_not_due_before_the_interval(self):
        self.assertFalse(self._due(now=86399.0))

    @override_settings(ENGINE_GC_DEEP_CLEAN_INTERVAL=86400, ENGINE_GC_DEEP_CLEAN_MAX_DEFER=259200)
    def test_due_and_quiet_runs_a_full_collection(self):
        self.assertTrue(self._due(now=86400.0))

        gc_policy.gc.collect.assert_called_once_with()
        self.assertEqual(gc_policy._state.last_deep_clean, 86400.0)

    @override_settings(ENGINE_GC_DEEP_CLEAN_INTERVAL=86400, ENGINE_GC_DEEP_CLEAN_MAX_DEFER=259200)
    def test_players_connected_defer_the_pause(self):
        self.sessions = 3

        self.assertFalse(self._due(now=86400.0 + self.HOUR))
        gc_policy.gc.collect.assert_not_called()

    @override_settings(ENGINE_GC_DEEP_CLEAN_INTERVAL=86400, ENGINE_GC_DEEP_CLEAN_MAX_DEFER=259200)
    def test_the_deferral_is_bounded(self):
        self.sessions = 3

        self.assertFalse(self._due(now=259199.0))
        self.assertTrue(self._due(now=259200.0))

    @override_settings(ENGINE_GC_DEEP_CLEAN_INTERVAL=0)
    def test_zero_interval_disables_deep_cleans(self):
        self.assertFalse(self._due(now=10**9))

    @override_settings(ENGINE_GC_DEEP_CLEAN_INTERVAL=86400)
    def test_never_runs_when_the_collector_is_not_managed(self):
        gc_policy._state.managed = False

        self.assertFalse(self._due(now=10**9))

    def test_a_deep_clean_reports_its_cost(self):
        with patch.object(gc_policy.logger, "log_info") as info:
            result = gc_policy.deep_clean("test")

        self.assertEqual(result.collected, 42)
        message = info.call_args.args[0]
        self.assertIn("42", message)
        self.assertIn("test", message)


class TestRecordMetric(SimpleTestCase):
    def test_record_gc_pause_is_a_noop_when_metrics_are_unavailable(self):
        from evennia.server import prometheus_metrics

        with patch.object(prometheus_metrics, "_init_metrics", return_value=False):
            self.assertIsNone(prometheus_metrics.record_gc_pause(2, 0.5))

    def test_record_gc_pause_labels_the_generation(self):
        from evennia.server import prometheus_metrics

        histogram = Mock()
        with (
            patch.object(prometheus_metrics, "_init_metrics", return_value=True),
            patch.object(prometheus_metrics, "GC_PAUSE_SECONDS", histogram),
        ):
            prometheus_metrics.record_gc_pause(2, 0.5)
            prometheus_metrics.record_gc_pause(99, 0.5)

        histogram.labels.assert_any_call(generation="2")
        histogram.labels.assert_any_call(generation="other")

    def test_record_reactor_stall_falls_back_to_one_label(self):
        from evennia.server import prometheus_metrics

        histogram = Mock()
        with (
            patch.object(prometheus_metrics, "_init_metrics", return_value=True),
            patch.object(prometheus_metrics, "REACTOR_STALL_SECONDS", histogram),
        ):
            prometheus_metrics.record_reactor_stall(0.4, "gc")
            prometheus_metrics.record_reactor_stall(0.4, "typed by an operator")

        histogram.labels.assert_any_call(cause="gc")
        histogram.labels.assert_any_call(cause="unknown")
