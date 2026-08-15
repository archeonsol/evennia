"""
Tests for evennia.utils.reactor_watchdog.

The stall detection is exercised by driving `_tick` with an injected monotonic
clock, so the test is deterministic and needs no running reactor: an
over-threshold gap between ticks must produce exactly one warning line, and
normal ticks none. The start/stop wiring (LoopingCall, disabled-when-zero) is
checked separately.
"""

from unittest.mock import patch

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
