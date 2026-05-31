"""
Tests for evennia.utils.reactor_watchdog.

The stall detection is exercised by driving `_tick` with an injected monotonic
clock, so the test is deterministic and needs no running reactor: an
over-threshold gap between ticks must produce exactly one warning line, and
normal ticks none. The start/stop wiring (LoopingCall, disabled-when-zero) is
checked separately.
"""

from unittest.mock import patch

from django.test import override_settings

from evennia.utils import reactor_watchdog
from evennia.utils.reactor_watchdog import ReactorStallWatchdog
from evennia.utils.test_resources import BaseEvenniaTestCase


class _Clock:
    """Manually advanced monotonic clock (seconds)."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestStallDetection(BaseEvenniaTestCase):
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


class TestLifecycle(BaseEvenniaTestCase):
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
