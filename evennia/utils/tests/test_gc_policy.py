"""
Tests for evennia.utils.gc_policy.

Collector behavior is exercised against fakes: a clock the test advances, a loop
that records ``call_later``, and patched ``gc`` entry points. Nothing here runs a
real collection or leaves the interpreter's collector disabled.
"""

import gc
import weakref
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


class _ManagedCase(_PolicyTestCase):
    """The managed policy applied over fakes: a scripted clock, loop, process and collector.

    The clock starts at 100 s with the boot collection at that moment, the process at
    600 MB, the interpreter holding 10 million blocks, and every collection freeing
    50,000 objects unless a test says otherwise.
    """

    def setUp(self):
        super().setUp()
        self.loop = _Loop()
        self.rss = 600.0
        self.blocks = 10_000_000
        self.freed = 50_000
        self.gc = {}
        for name in ("collect", "freeze", "disable", "enable", "get_count", "isenabled"):
            patcher = patch.object(gc_policy.gc, name)
            self.gc[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.gc["get_count"].return_value = (0, 0, 0)
        self.gc["isenabled"].return_value = True
        self.gc["collect"].side_effect = lambda *args: self.freed
        for patcher in (
            patch.object(gc_policy.clock, "get_bound_loop", return_value=self.loop),
            patch.object(gc_policy, "_rss_mb", lambda: self.rss),
            patch.object(gc_policy, "_allocated_blocks", lambda: self.blocks),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        gc_policy.apply_gc_policy()
        self.gc["collect"].reset_mock()  # forget the boot collection

    def tick(self, advance=0.05):
        """Let the loop timer fire after ``advance`` seconds."""
        self.clock.advance(advance)
        self.loop.fire_last()

    def full_collections(self):
        return [call for call in self.gc["collect"].call_args_list if call.args == ()]


@override_settings(
    ENGINE_GC_POLICY="managed",
    ENGINE_GC_RECLAIM_GROWTH_PERCENT=15,
    ENGINE_GC_RECLAIM_MIN_INTERVAL=300,
)
class TestRequestedReclaim(_ManagedCase):
    def test_a_request_runs_a_full_collection_between_turns(self):
        self.clock.advance(400)

        self.assertTrue(gc_policy.request_reclaim())
        self.assertEqual(self.full_collections(), [], "nothing runs inside the request")
        self.tick()

        self.assertEqual(len(self.full_collections()), 1)

    def test_the_request_waits_out_the_minimum_interval(self):
        self.clock.advance(100)  # 100 s after the boot collection
        gc_policy.request_reclaim()
        self.tick()
        self.assertEqual(self.full_collections(), [])

        self.tick(250.0)  # 350 s after it

        self.assertEqual(len(self.full_collections()), 1)

    def test_a_burst_of_requests_costs_one_collection(self):
        self.clock.advance(400)
        for _ in range(5):
            gc_policy.request_reclaim()

        self.tick()
        self.tick()

        self.assertEqual(len(self.full_collections()), 1)

    def test_a_request_made_while_one_is_spaced_out_is_kept_not_dropped(self):
        self.clock.advance(400)
        gc_policy.request_reclaim()
        self.tick()  # runs
        gc_policy.request_reclaim()
        self.tick(10.0)  # too soon after that one
        self.assertEqual(len(self.full_collections()), 1)

        self.tick(300.0)

        self.assertEqual(len(self.full_collections()), 2)

    def test_nothing_is_requested_without_the_managed_policy(self):
        gc_policy.stop_gc_policy()

        self.assertFalse(gc_policy.request_reclaim())

    def test_the_log_names_the_reason_and_the_sizes_either_side(self):
        self.clock.advance(400)
        self.rss, self.blocks = 1500.0, 12_000_000

        def collect(*args):
            self.rss, self.blocks = 1100.0, 9_000_000
            return 90_000

        self.gc["collect"].side_effect = collect
        gc_policy.request_reclaim()

        with patch.object(gc_policy.logger, "log_info") as info:
            self.tick()

        message = info.call_args.args[0]
        self.assertIn("(requested)", message)
        self.assertIn("90000 unreachable objects", message)
        self.assertIn("allocated blocks 12000000 -> 9000000", message)
        self.assertIn("process 1500 -> 1100 MB", message)

    def test_the_metrics_count_the_reason_and_what_was_freed(self):
        self.clock.advance(400)
        gc_policy.request_reclaim()

        with patch.object(gc_policy.prometheus_metrics, "record_gc_reclaim") as record:
            self.tick()

        record.assert_called_once_with("requested", 50_000)

    def test_an_unlisted_reason_is_counted_as_requested(self):
        self.clock.advance(400)
        gc_policy.request_reclaim("a mass delete")

        with patch.object(gc_policy.prometheus_metrics, "record_gc_reclaim") as record:
            self.tick()

        record.assert_called_once_with("requested", 50_000)


@override_settings(
    ENGINE_GC_POLICY="managed",
    ENGINE_GC_RECLAIM_GROWTH_PERCENT=15,
    ENGINE_GC_RECLAIM_MIN_INTERVAL=300,
)
class TestReclaimAfterGrowth(_ManagedCase):
    def test_growth_past_the_threshold_runs_a_full_collection(self):
        self.clock.advance(400)
        self.blocks = 11_400_000  # 14% over the boot heap: not enough
        self.tick(16.0)
        self.assertEqual(self.full_collections(), [])

        self.blocks = 11_600_000  # 16% over it
        self.tick(16.0)

        self.assertEqual(len(self.full_collections()), 1)

    def test_garbage_filling_the_allocators_pool_still_counts_as_growth(self):
        """Why growth is counted in blocks: freed memory is reused, so the process stops growing."""
        self.clock.advance(400)
        self.rss = 600.0
        self.blocks = 12_500_000

        self.tick(16.0)

        self.assertEqual(len(self.full_collections()), 1)

    def test_the_blocks_are_only_counted_every_few_seconds(self):
        self.clock.advance(400)
        self.tick(16.0)
        counts = []
        with patch.object(
            gc_policy, "_allocated_blocks", side_effect=lambda: counts.append(1) or 10_000_000
        ):
            for _ in range(20):
                self.tick(0.05)  # a second of ticks

        self.assertEqual(counts, [])

    def test_growth_is_measured_from_where_the_last_collection_left_the_heap(self):
        self.clock.advance(400)

        def collect(*args):
            self.blocks = 9_000_000  # the collection frees a tenth of the heap
            return 50_000

        self.gc["collect"].side_effect = collect
        self.blocks = 11_600_000
        self.tick(16.0)
        self.assertEqual(len(self.full_collections()), 1)

        self.blocks = 10_300_000  # 14% over the 9 million it was left at
        self.tick(400.0)
        self.assertEqual(len(self.full_collections()), 1)

        self.blocks = 10_400_000  # 15.5% over
        self.tick(16.0)

        self.assertEqual(len(self.full_collections()), 2)

    def test_growth_does_not_collect_inside_the_minimum_interval(self):
        self.clock.advance(100)
        self.blocks = 40_000_000

        self.tick(16.0)

        self.assertEqual(self.full_collections(), [])

    @override_settings(ENGINE_GC_RECLAIM_GROWTH_PERCENT=0)
    def test_zero_turns_the_growth_trigger_off(self):
        self.clock.advance(400)
        self.blocks = 10**9

        self.tick(16.0)

        self.assertEqual(self.full_collections(), [])

    def test_a_collection_that_finds_little_doubles_what_the_next_one_waits_for(self):
        self.freed = 100  # growth that was live data
        self.clock.advance(400)
        self.blocks = 11_600_000
        self.tick(16.0)
        self.assertEqual(len(self.full_collections()), 1)
        self.assertEqual(gc_policy._state.growth_factor, 2)

        self.blocks = 14_000_000  # 20.7% over 11.6 million: enough for 15%, not for 30%
        self.tick(400.0)
        self.assertEqual(len(self.full_collections()), 1)

        self.blocks = 15_200_000  # 31% over
        self.tick(16.0)

        self.assertEqual(len(self.full_collections()), 2)

    def test_the_wait_stops_doubling_at_a_cap(self):
        self.freed = 0
        for _ in range(8):
            self.clock.advance(400)
            self.blocks = int(self.blocks * (1 + 0.15 * gc_policy._state.growth_factor) + 10)
            self.tick(16.0)

        self.assertEqual(gc_policy._state.growth_factor, gc_policy._MAX_GROWTH_FACTOR)

    def test_a_collection_that_finds_a_lot_resets_the_wait(self):
        gc_policy._state.growth_factor = 4
        self.freed = 100_000
        self.clock.advance(400)
        self.blocks = 16_100_000  # 61% over: past 4 x 15%

        self.tick(16.0)

        self.assertEqual(len(self.full_collections()), 1)
        self.assertEqual(gc_policy._state.growth_factor, 1)


class _Node:
    """A reference cycle's member, with a weak reference allowed."""


@override_settings(ENGINE_GC_POLICY="default")
class TestReclaimFreesRealGarbage(SimpleTestCase):
    """The reason the policy needs a full collection at all, against the real collector."""

    def setUp(self):
        super().setUp()
        gc_policy._reset_for_tests()
        self.addCleanup(gc_policy._reset_for_tests)
        self._was_enabled = gc.isenabled()
        gc.collect()
        gc.disable()  # no automatic pass may free the garbage under test
        self.addCleanup(lambda: gc.enable() if self._was_enabled else None)

    def test_a_cycle_that_outlived_a_young_pass_is_freed_only_by_a_full_collection(self):
        refs, keep = [], []
        for _ in range(50):
            first, second = _Node(), _Node()
            first.peer, second.peer = second, first
            refs.append(weakref.ref(first))
            keep.append(first)
        del first, second
        gc.collect(0)  # the cycles are alive here, so they are promoted
        gc.collect(0)
        keep.clear()  # ...and die later

        gc.collect(0)
        alive_after_a_young_pass = sum(ref() is not None for ref in refs)
        result = gc_policy.deep_clean("manual")
        alive_after_a_deep_clean = sum(ref() is not None for ref in refs)

        self.assertEqual(alive_after_a_young_pass, 50)
        self.assertEqual(alive_after_a_deep_clean, 0)
        self.assertGreaterEqual(result.collected, 100)


class TestRecordMetric(SimpleTestCase):
    def test_the_memory_gauges_answer_when_scraped(self):
        from prometheus_client import REGISTRY

        from evennia.server import prometheus_metrics

        if not prometheus_metrics._init_metrics():
            self.skipTest("metrics are disabled")

        for name in (
            "evennia_idmapper_cached_instances",
            "evennia_gc_frozen_objects",
            "evennia_python_allocated_blocks",
        ):
            self.assertIsNotNone(REGISTRY.get_sample_value(name), name)
        self.assertGreater(REGISTRY.get_sample_value("evennia_python_allocated_blocks"), 0)

    def test_record_gc_reclaim_counts_the_reason_and_the_objects(self):
        from evennia.server import prometheus_metrics

        reasons, objects = Mock(), Mock()
        with (
            patch.object(prometheus_metrics, "_init_metrics", return_value=True),
            patch.object(prometheus_metrics, "GC_RECLAIM_TOTAL", reasons),
            patch.object(prometheus_metrics, "GC_RECLAIMED_OBJECTS_TOTAL", objects),
        ):
            prometheus_metrics.record_gc_reclaim("growth", 1234)
            prometheus_metrics.record_gc_reclaim("typed by an operator", 0)

        reasons.labels.assert_any_call(reason="growth")
        reasons.labels.assert_any_call(reason="manual")
        objects.inc.assert_called_once_with(1234)

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
