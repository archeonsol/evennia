"""
Reactor-stall watchdog: warn when a single reactor turn blocks too long,
and actively sample the live stack trace of the blocking IO thread.

Evennia runs the game on one reactor thread (see `evennia.utils.defer` for the
blessed way to push blocking I/O off it). When a command, hook, or script blocks
that thread, *every* connected player freezes for the duration. This watchdog is
the cheap instrument that *finds* those offenders: it logs a warning whenever a
single reactor turn exceeds `REACTOR_STALL_WARNING_MS`.

In addition to post-stall duration measurement via LoopingCall, a background
daemon thread periodically monitors the reactor heartbeat. If the reactor is
actively stalled beyond `REACTOR_STALL_SAMPLE_MS` (default: 1500ms), it captures
and logs the live stack trace of the IO thread using `sys._current_frames()` to
pinpoint the exact blocking code site.
"""

import sys
import threading
import time
import traceback

from django.conf import settings

from evennia.utils import clock, logger

#: Default poll interval as a fraction of the threshold, floored, so the
#: measured stall stays close to the true block duration without polling
#: needlessly often.
_INTERVAL_FRACTION = 0.25
_MIN_INTERVAL = 0.025  # seconds
_MAX_INTERVAL = 1.0  # seconds


class ReactorStallWatchdog:
    """
    Periodic check and background sampler that logs warnings and live stack traces
    when a reactor turn blocks too long.

    Args:
        threshold_ms (float, optional): Stall threshold in milliseconds. A
            reactor turn that blocks longer than this logs one warning. `0`
            disables the watchdog (`start` becomes a no-op). Defaults to the
            `REACTOR_STALL_WARNING_MS` setting.
        interval (float, optional): Poll interval in seconds. Defaults to a
            fraction of the threshold, clamped to a sane range.
        sample_threshold_ms (float, optional): Duration in ms before the background
            thread samples and logs the live stack trace of the stalled IO thread.
            Defaults to max(1500.0, threshold_ms * 3).
        _now (callable, optional): Monotonic clock returning seconds, injectable
            for testing. Defaults to `time.perf_counter`.

    """

    def __init__(
        self,
        threshold_ms=None,
        interval=None,
        sample_threshold_ms=None,
        _now=time.perf_counter,
    ):
        if threshold_ms is None:
            threshold_ms = getattr(settings, "REACTOR_STALL_WARNING_MS", 200)
        self.threshold_ms = float(threshold_ms or 0)
        if interval is None:
            interval = max(
                _MIN_INTERVAL,
                min(_MAX_INTERVAL, (self.threshold_ms / 1000.0) * _INTERVAL_FRACTION),
            )
        self.interval = interval
        if sample_threshold_ms is None:
            sample_threshold_ms = getattr(
                settings, "REACTOR_STALL_SAMPLE_MS", max(1500.0, self.threshold_ms * 3)
            )
        self.sample_threshold_ms = float(sample_threshold_ms or 1500.0)
        self._now = _now
        self._last = None
        self._last_heartbeat = None
        self._loop = clock.make_looping(self._tick)
        self._running = False
        self._sampler_thread = None
        self._target_thread_id = None
        self._state_lock = threading.Lock()
        self._heartbeat_episode = 0
        self._sample_episode = None
        self._last_sample_log_time = 0.0

    @property
    def enabled(self) -> bool:
        """bool: Whether the watchdog is active (threshold above zero)."""
        return self.threshold_ms > 0

    def start(self) -> None:
        """Start ticking and background sampling. No-op if disabled or already running."""
        if not self.enabled or self._loop.running:
            return
        self._last = None
        with self._state_lock:
            self._last_heartbeat = self._now()
            self._heartbeat_episode += 1
            self._sample_episode = None
            self._last_sample_log_time = 0.0
        self._running = True
        self._target_thread_id = clock.get_loop_thread_id() or (
            threading.main_thread().ident if threading.main_thread() else None
        )
        self._loop.start(self.interval, now=False)
        self._start_sampler_thread()

    def stop(self) -> None:
        """Stop ticking and background sampling. Safe to call when not running."""
        self._running = False
        if self._loop.running:
            self._loop.stop()

    def _start_sampler_thread(self) -> None:
        """Start background daemon sampler thread if not already running."""
        if self._sampler_thread is not None and self._sampler_thread.is_alive():
            return
        self._sampler_thread = threading.Thread(
            target=self._sampler_loop,
            name="ReactorStallSampler",
            daemon=True,
        )
        self._sampler_thread.start()

    def _sampler_loop(self) -> None:
        """Background thread that sleeps and checks if the reactor thread is frozen."""
        sample_interval = max(0.1, min(0.5, self.sample_threshold_ms / 3000.0))
        while self._running:
            time.sleep(sample_interval)
            if not self._running:
                break
            self._sample_stall(self._now())

    def _stall_candidate(self, now):
        """Return ``(elapsed, episode)`` for a current qualifying stall."""
        with self._state_lock:
            last = self._last_heartbeat
            episode = self._heartbeat_episode
        if last is None:
            return None
        elapsed_s = now - last
        if elapsed_s < self.sample_threshold_ms / 1000.0:
            return None
        return elapsed_s, episode

    def _reserve_sample(self, episode, now):
        """Atomically reserve a rate-limited sample for one current episode."""
        with self._state_lock:
            if episode != self._heartbeat_episode:
                return False
            if self._sample_episode == episode and now - self._last_sample_log_time < 5.0:
                return False
            self._sample_episode = episode
            self._last_sample_log_time = now
            return True

    def _sample_stall(self, now):
        """Capture one live stack if a current episode is eligible."""
        candidate = self._stall_candidate(now)
        if candidate is None:
            return False
        elapsed_s, episode = candidate
        if not self._reserve_sample(episode, now):
            return False
        target_id = self._target_thread_id or (
            clock.get_loop_thread_id()
            or (threading.main_thread().ident if threading.main_thread() else None)
        )
        frame = sys._current_frames().get(target_id) if target_id else None
        if frame:
            stack_str = "".join(traceback.format_stack(frame))
            logger.log_warn(
                f"Live reactor stall in progress (~{elapsed_s * 1000.0:.0f}ms blocked)! "
                f"Live IO thread execution stack:\n{stack_str}"
            )
            return True
        return False

    def _tick(self) -> None:
        """Measure the gap since the last tick; warn if it exceeds threshold."""
        now = self._now()
        with self._state_lock:
            self._last_heartbeat = now
            self._heartbeat_episode += 1
        if self._last is not None:
            stall_ms = (now - self._last - self.interval) * 1000.0
            if stall_ms > self.threshold_ms:
                logger.log_warn(
                    "Reactor stall: a single reactor turn blocked for ~%.0fms "
                    "(threshold %.0fms). Move blocking I/O off the reactor with "
                    "evennia.utils.defer." % (stall_ms, self.threshold_ms)
                )
        self._last = now
