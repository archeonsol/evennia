"""
Reactor-stall watchdog: warn when a single reactor turn blocks too long.

Evennia runs the game on one reactor thread (see `evennia.utils.defer` for the
blessed way to push blocking I/O off it). When a command, hook, or script blocks
that thread, *every* connected player freezes for the duration. This watchdog is
the cheap instrument that *finds* those offenders: it logs a warning whenever a
single reactor turn exceeds `REACTOR_STALL_WARNING_MS`, turning "something feels
laggy" into a concrete worklist of blocking sites to move onto
`evennia.utils.defer`.

Mechanism: a `LoopingCall` ticks on the reactor at a fixed interval. Because the
call runs *on* the reactor thread, it cannot fire while the reactor is blocked;
it fires late once the block clears. The lateness (measured gap minus the
expected interval) is how long the reactor stalled. One stall produces one late
tick, hence exactly one warning.

This is a coarse instrument by design: the measured stall underestimates the
true block by up to one poll interval, so blocks just over the threshold may be
reported a hair under it or missed, while clear offenders are caught reliably. It
observes from the side; it does not wrap or instrument any call on the hot path.

It cannot name the culprit: by the time a late tick fires the blocking call has
already returned, so there is no live stack to capture. The warning reports the
stall duration; pair it with normal profiling to localize the site.
"""

import time

from django.conf import settings
from twisted.internet.task import LoopingCall

from evennia.utils import logger

#: Default poll interval as a fraction of the threshold, floored, so the
#: measured stall stays close to the true block duration without polling
#: needlessly often.
_INTERVAL_FRACTION = 0.25
_MIN_INTERVAL = 0.025  # seconds
_MAX_INTERVAL = 1.0  # seconds


class ReactorStallWatchdog:
    """
    Periodic check that logs a warning when a reactor turn blocks too long.

    Args:
        threshold_ms (float, optional): Stall threshold in milliseconds. A
            reactor turn that blocks longer than this logs one warning. `0`
            disables the watchdog (`start` becomes a no-op). Defaults to the
            `REACTOR_STALL_WARNING_MS` setting.
        interval (float, optional): Poll interval in seconds. Defaults to a
            fraction of the threshold, clamped to a sane range.
        _now (callable, optional): Monotonic clock returning seconds, injectable
            for testing. Defaults to `time.perf_counter`.

    """

    def __init__(self, threshold_ms=None, interval=None, _now=time.perf_counter):
        if threshold_ms is None:
            threshold_ms = getattr(settings, "REACTOR_STALL_WARNING_MS", 200)
        self.threshold_ms = float(threshold_ms or 0)
        if interval is None:
            interval = max(
                _MIN_INTERVAL,
                min(_MAX_INTERVAL, (self.threshold_ms / 1000.0) * _INTERVAL_FRACTION),
            )
        self.interval = interval
        self._now = _now
        self._last = None
        self._loop = LoopingCall(self._tick)

    @property
    def enabled(self) -> bool:
        """bool: Whether the watchdog is active (threshold above zero)."""
        return self.threshold_ms > 0

    def start(self) -> None:
        """Start ticking. No-op if disabled or already running."""
        if not self.enabled or self._loop.running:
            return
        self._last = None
        self._loop.start(self.interval, now=False)

    def stop(self) -> None:
        """Stop ticking. Safe to call when not running."""
        if self._loop.running:
            self._loop.stop()

    def _tick(self) -> None:
        """Measure the gap since the last tick; warn if it exceeds threshold."""
        now = self._now()
        if self._last is not None:
            stall_ms = (now - self._last - self.interval) * 1000.0
            if stall_ms > self.threshold_ms:
                logger.log_warn(
                    "Reactor stall: a single reactor turn blocked for ~%.0fms "
                    "(threshold %.0fms). Move blocking I/O off the reactor with "
                    "evennia.utils.defer." % (stall_ms, self.threshold_ms)
                )
        self._last = now
