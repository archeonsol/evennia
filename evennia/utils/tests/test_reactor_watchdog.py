"""
Tests for evennia.utils.reactor_watchdog.

The stall detection is exercised by driving `_tick` with an injected monotonic
clock, so the test is deterministic and needs no running reactor: an
over-threshold gap between ticks must produce exactly one warning line, and
normal ticks none. The start/stop wiring (LoopingCall, disabled-when-zero) is
checked separately.
"""

import os
import sysconfig
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from evennia.utils import reactor_watchdog
from evennia.utils.reactor_watchdog import ReactorStallWatchdog


class _Clock:
    """Manually advanced monotonic clock (seconds)."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestStallDetection(SimpleTestCase):
    def _make(self, clock, threshold_ms=200, interval=0.05):
        return ReactorStallWatchdog(threshold_ms=threshold_ms, interval=interval, _now=clock)

    def test_over_threshold_block_warns_exactly_once(self):
        clock = _Clock()
        wd = self._make(clock)

        with patch.object(reactor_watchdog.logger, "log_warn") as mock_warn:
            wd._tick()  # t=0, primes last; no warn
            clock.advance(0.05)
            wd._tick()  # normal interval; gap == interval -> no warn
            clock.advance(0.05 + 0.30)  # a 300ms reactor block past this tick
            wd._tick()  # late tick -> exactly one warn
            clock.advance(0.05)
            wd._tick()  # back to normal; no further warn

        self.assertEqual(mock_warn.call_count, 1)
        msg = mock_warn.call_args[0][0]
        self.assertIn("Reactor stall", msg)
        self.assertIn("300ms", msg)

    def test_under_threshold_jitter_does_not_warn(self):
        clock = _Clock()
        wd = self._make(clock)

        with patch.object(reactor_watchdog.logger, "log_warn") as mock_warn:
            wd._tick()
            for _ in range(5):
                clock.advance(0.05 + 0.10)  # 100ms lateness, under 200ms threshold
                wd._tick()

        mock_warn.assert_not_called()


def _frames(*sites):
    """A frame chain from (path, line, function) sites, innermost first."""
    frame = None
    for path, line, name in reversed(sites):
        frame = SimpleNamespace(
            f_code=SimpleNamespace(co_filename=path, co_name=name),
            f_lineno=line,
            f_back=frame,
        )
    return frame


_STDLIB_SITE = (
    os.path.join(sysconfig.get_paths()["stdlib"], "json", "encoder.py"),
    10,
    "encode",
)
_ENGINE_SITE = (
    os.path.join(reactor_watchdog._ENGINE_DIR, "server", "server.py"),
    20,
    "run",
)


def _game_site(relpath, line, name):
    return (os.path.join(settings.GAME_DIR, relpath), line, name)


class TestWorkLabel(SimpleTestCase):
    def test_names_innermost_and_outermost_game_frames(self):
        frame = _frames(
            _STDLIB_SITE,
            _game_site("world/medical/clinic_stock.py", 88, "_stock_counts"),
            _game_site("world/food/courier_stock.py", 12, "iter_restock_demands"),
            _game_site("world/rpg/courier.py", 300, "generate"),
            _ENGINE_SITE,
        )

        self.assertEqual(
            reactor_watchdog._work_label(frame),
            "world/medical/clinic_stock.py:88 _stock_counts via world/rpg/courier.py:300 generate",
        )

    def test_falls_back_to_innermost_engine_frame(self):
        frame = _frames(_STDLIB_SITE, _ENGINE_SITE)

        self.assertEqual(reactor_watchdog._work_label(frame), "evennia/server/server.py:20 run")


class TestStallWorkInWarning(SimpleTestCase):
    def _make(self, clock):
        wd = ReactorStallWatchdog(threshold_ms=200, interval=0.05, _now=clock)
        wd._target_thread_id = 123
        return wd

    def test_turn_warning_names_work_sampled_during_the_stall(self):
        clock = _Clock()
        wd = self._make(clock)
        frame = _frames(_game_site("world/rpg/courier.py", 300, "generate"))

        with (
            patch.object(reactor_watchdog.sys, "_current_frames", return_value={123: frame}),
            patch.object(reactor_watchdog.logger, "log_warn") as mock_warn,
        ):
            wd._tick()
            clock.advance(0.30)
            wd._note_stalled_work(clock())
            clock.advance(0.05)
            wd._tick()

        self.assertIn("at world/rpg/courier.py:300 generate", mock_warn.call_args[0][0])

    def test_turn_warning_says_when_no_work_was_sampled(self):
        clock = _Clock()
        wd = self._make(clock)

        with patch.object(reactor_watchdog.logger, "log_warn") as mock_warn:
            wd._tick()
            clock.advance(0.35)
            wd._tick()

        self.assertIn("at work not sampled", mock_warn.call_args[0][0])

    def test_sample_from_an_earlier_stall_is_not_reused(self):
        clock = _Clock()
        wd = self._make(clock)
        frame = _frames(_game_site("world/rpg/courier.py", 300, "generate"))

        with (
            patch.object(reactor_watchdog.sys, "_current_frames", return_value={123: frame}),
            patch.object(reactor_watchdog.logger, "log_warn") as mock_warn,
        ):
            wd._tick()
            clock.advance(0.30)
            wd._note_stalled_work(clock())
            clock.advance(0.05)
            wd._tick()
            clock.advance(0.35)
            wd._tick()

        self.assertIn("at work not sampled", mock_warn.call_args[0][0])

    def test_no_sample_while_reactor_keeps_up(self):
        clock = _Clock()
        wd = self._make(clock)

        with patch.object(reactor_watchdog.sys, "_current_frames") as frames:
            wd._tick()
            clock.advance(0.10)
            self.assertIsNone(wd._note_stalled_work(clock()))

        frames.assert_not_called()


class TestLiveSampling(SimpleTestCase):
    """Live stacks are throttled within, never across, stall episodes."""

    def _make(self, clock):
        wd = ReactorStallWatchdog(
            threshold_ms=200,
            interval=0.05,
            sample_threshold_ms=1000,
            _now=clock,
        )
        wd._target_thread_id = 123
        wd._last_heartbeat = 0.0
        return wd

    def _sampling_patches(self):
        return (
            patch.object(reactor_watchdog.sys, "_current_frames", return_value={123: object()}),
            patch.object(reactor_watchdog.traceback, "format_stack", return_value=["stack"]),
            patch.object(reactor_watchdog.logger, "log_warn"),
        )

    def test_first_episode_samples_immediately_before_global_cooldown_age(self):
        clock = _Clock()
        wd = self._make(clock)
        clock.advance(2.0)

        frames, formatted, warning = self._sampling_patches()
        with frames, formatted, warning as mock_warn:
            self.assertTrue(wd._sample_stall(clock()))

        mock_warn.assert_called_once()

    def test_recovery_allows_second_episode_inside_five_seconds(self):
        clock = _Clock()
        wd = self._make(clock)
        frames, formatted, warning = self._sampling_patches()

        with frames, formatted, warning as mock_warn:
            clock.advance(2.0)
            self.assertTrue(wd._sample_stall(clock()))
            wd._tick()
            clock.advance(1.1)
            self.assertTrue(wd._sample_stall(clock()))

        self.assertEqual(mock_warn.call_count, 2)

    def test_continuing_episode_remains_rate_limited(self):
        clock = _Clock()
        wd = self._make(clock)
        frames, formatted, warning = self._sampling_patches()

        with frames, formatted, warning as mock_warn:
            clock.advance(2.0)
            self.assertTrue(wd._sample_stall(clock()))
            clock.advance(1.0)
            self.assertFalse(wd._sample_stall(clock()))
            clock.advance(4.1)
            self.assertTrue(wd._sample_stall(clock()))

        self.assertEqual(mock_warn.call_count, 2)

    def test_stale_episode_cannot_reserve_after_recovery(self):
        clock = _Clock()
        wd = self._make(clock)
        clock.advance(2.0)
        old_candidate = wd._stall_candidate(clock())

        wd._tick()

        self.assertFalse(wd._reserve_sample(old_candidate[1], clock()))
        clock.advance(1.1)
        new_candidate = wd._stall_candidate(clock())
        self.assertNotEqual(old_candidate[1], new_candidate[1])
        self.assertTrue(wd._reserve_sample(new_candidate[1], clock()))


class TestLifecycle(SimpleTestCase):
    def test_disabled_when_threshold_zero(self):
        wd = ReactorStallWatchdog(threshold_ms=0)
        self.assertFalse(wd.enabled)

        with patch.object(wd._loop, "start") as mock_start:
            wd.start()
        mock_start.assert_not_called()
        self.assertFalse(wd._loop.running)

    def test_start_runs_looping_call_and_stop_halts_it(self):
        wd = ReactorStallWatchdog(threshold_ms=200, interval=0.05)
        with patch.object(wd._loop, "start") as mock_start:
            wd.start()
        mock_start.assert_called_once_with(0.05, now=False)

        # stop() is a no-op when the loop never actually ran
        wd._loop.running = False
        wd.stop()  # should not raise

    @override_settings(REACTOR_STALL_WARNING_MS=350)
    def test_threshold_defaults_to_setting(self):
        wd = ReactorStallWatchdog()
        self.assertEqual(wd.threshold_ms, 350.0)
        # interval derives from threshold, clamped into range
        self.assertGreaterEqual(wd.interval, reactor_watchdog._MIN_INTERVAL)
        self.assertLessEqual(wd.interval, reactor_watchdog._MAX_INTERVAL)


class TestGcAttribution(SimpleTestCase):
    """A stall the collector caused is named as the collector, not as the stack."""

    def _stall(self, probe, *, sampled_work=None, stall=0.30):
        """Drive one stall of ``stall`` seconds and return the warning and recorded cause."""
        clock = _Clock()
        wd = ReactorStallWatchdog(threshold_ms=200, interval=0.05, _now=clock, _gc_probe=probe)
        wd._target_thread_id = 123
        frames = {123: _frames(_game_site(*sampled_work))} if sampled_work else {}
        with (
            patch.object(reactor_watchdog.sys, "_current_frames", return_value=frames),
            patch.object(reactor_watchdog.logger, "log_warn") as warn,
            patch.object(reactor_watchdog.prometheus_metrics, "record_reactor_stall") as record,
        ):
            wd._tick()
            clock.advance(0.05)
            if sampled_work:
                wd._note_stalled_work(clock() + stall)
            clock.advance(stall)
            wd._tick()
        return warn, record

    def test_a_stall_mostly_inside_the_collector_names_the_collection(self):
        probe = Mock(return_value=(0.28, (1.0, 1.28, 2, 1234)))

        warn, record = self._stall(probe)

        message = warn.call_args.args[0]
        self.assertIn("at garbage collection (generation 2, 280ms, 1234 objects collected", message)
        self.assertIn("ENGINE_GC_POLICY", message)
        self.assertNotIn("Move blocking I/O", message)
        self.assertEqual(record.call_args.args[1], "gc")

    def test_the_probe_is_asked_about_the_blocked_span(self):
        probe = Mock(return_value=(0.0, None))

        self._stall(probe)

        start, end = probe.call_args.args
        self.assertAlmostEqual(start, 0.05)
        self.assertAlmostEqual(end, 0.35)

    def test_the_sampled_work_is_kept_alongside_the_collection(self):
        probe = Mock(return_value=(0.28, (1.0, 1.28, 1, 7)))

        warn, _record = self._stall(probe, sampled_work=("world/rpg/courier.py", 300, "generate"))

        self.assertIn("; running world/rpg/courier.py:300 generate)", warn.call_args.args[0])

    def test_a_minor_collector_share_does_not_hide_the_blocking_code(self):
        probe = Mock(return_value=(0.05, (1.0, 1.05, 1, 7)))

        warn, record = self._stall(probe, sampled_work=("world/rpg/courier.py", 300, "generate"))

        message = warn.call_args.args[0]
        self.assertIn(
            "at world/rpg/courier.py:300 generate (garbage collection took 50ms of it)", message
        )
        self.assertIn("Move blocking I/O", message)
        self.assertEqual(record.call_args.args[1], "work")

    def test_no_collector_time_leaves_the_message_as_it_was(self):
        warn, record = self._stall(Mock(return_value=(0.0, None)))

        self.assertIn("at work not sampled. Move blocking I/O", warn.call_args.args[0])
        self.assertEqual(record.call_args.args[1], "unknown")


class TestSamplerCadence(SimpleTestCase):
    def test_wakes_often_enough_to_catch_a_stall_barely_past_the_threshold(self):
        for sample_ms in (250, 1500):
            with self.subTest(sample_ms=sample_ms):
                wd = ReactorStallWatchdog(threshold_ms=200, sample_threshold_ms=sample_ms)
                self.assertLessEqual(wd.sample_interval, 0.03)

    def test_cadence_scales_with_a_larger_threshold(self):
        wd = ReactorStallWatchdog(threshold_ms=2000, sample_threshold_ms=2000)
        self.assertAlmostEqual(wd.sample_interval, 0.25)
