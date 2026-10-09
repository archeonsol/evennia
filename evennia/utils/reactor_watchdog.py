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

A stall is not always the code that was running. When the collector accounts for
most of it (see `evennia.utils.gc_policy`) the warning names the collection, with
its generation and length, instead of blaming whatever frame happened to be on the
stack when the pause landed.

Nor is it always the loop's own code that is slow. A thread that holds the GIL
through one long C call (counting a million objects, a big `json.dumps`, a regex)
leaves the loop thread waiting at whatever frame last let go of it, which is
usually the event loop's own idle wait: the stack names no culprit. The watchdog
therefore also reads how much CPU every thread used between heartbeats. A stall in
which the loop used little and one other thread used most of the window is reported
as a GIL wait and names that thread, and the live stack sample dumps the stacks of
the threads that were burning CPU beside the loop's. (This found a metrics gauge
that kept the loop waiting for half a second every few seconds, while the stack of
every stall said `loop.run_forever()`.)
"""

import os
import sys
import sysconfig
import threading
import time
import traceback

from django.conf import settings

from evennia.server import prometheus_metrics
from evennia.utils import clock, gc_policy, logger

#: Default poll interval as a fraction of the threshold, floored, so the
#: measured stall stays close to the true block duration without polling
#: needlessly often.
_INTERVAL_FRACTION = 0.25
_MIN_INTERVAL = 0.025  # seconds
_MAX_INTERVAL = 1.0  # seconds

#: A thread other than the loop is "busy" in a stalled window if it used at least this
#: share of the window in CPU.
_BUSY_SHARE = 0.25
#: The loop counts as waiting, not working, if it used no more than this share of the
#: window in CPU.
_LOOP_IDLE_SHARE = 0.3
#: The GIL is blamed on a thread only if it used at least this share of the window.
_HOLDER_SHARE = 0.5
#: Threads named in one warning, busiest first.
_MAX_NAMED_THREADS = 3
#: Innermost frames of a busy thread's stack kept in the live sample.
_BUSY_STACK_FRAMES = 12

_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIBRARY_DIRS = tuple(
    {
        os.path.abspath(path) + os.sep
        for name in ("stdlib", "platstdlib", "purelib", "platlib")
        if (path := sysconfig.get_paths().get(name))
    }
)


def _short_path(filename):
    """``filename`` relative to the deepest of the game and engine checkouts holding it."""
    path = os.path.abspath(filename)
    roots = [
        os.path.abspath(r)
        for r in (os.path.dirname(_ENGINE_DIR), getattr(settings, "GAME_DIR", None))
        if r
    ]
    for root in sorted(roots, key=len, reverse=True):
        if path.startswith(root + os.sep):
            # forward slashes everywhere, so a log line reads the same on any host
            return os.path.relpath(path, root).replace(os.sep, "/")
    return path


def _thread_cpu_seconds():
    """Return the CPU seconds each live thread has used so far, by thread ident.

    Reads the clocks the operating system keeps per thread, so a reading costs one
    system call per thread and needs nothing from the thread being read. Threads that
    ended between listing and reading are left out.

    Returns:
        dict: ``{ident: seconds}``; empty where the platform keeps no per-thread
        clock (Windows), which turns GIL attribution off rather than guessing.
    """
    clock_of = getattr(time, "pthread_getcpuclockid", None)
    read = getattr(time, "clock_gettime", None)
    if clock_of is None or read is None:
        return {}
    readings = {}
    for thread in threading.enumerate():
        ident = thread.ident
        if ident is None:
            continue
        try:
            readings[ident] = read(clock_of(ident))
        except (OSError, ValueError, OverflowError):
            continue
    return readings


def _frame_label(frame):
    code = frame.f_code
    return f"{_short_path(code.co_filename)}:{frame.f_lineno} {code.co_name}"


def _work_label(frame):
    """Locate blocked work: ``inner via outer`` game frames, else the innermost engine frame.

    The innermost game frame is where the time is going; the outermost is the
    entry point that started it (a system run, a command). With no game code on
    the stack the innermost engine frame is named, then the innermost frame.
    """
    game, engine, innermost = [], None, None
    while frame is not None:
        code = getattr(frame, "f_code", None)
        if code is not None:
            filename = code.co_filename
            innermost = innermost or frame
            path = os.path.abspath(filename)
            if path.startswith(_ENGINE_DIR + os.sep):
                engine = engine or frame
            elif not filename.startswith("<") and not path.startswith(_LIBRARY_DIRS):
                game.append(frame)
        frame = getattr(frame, "f_back", None)
    if game:
        inner, outer = _frame_label(game[0]), _frame_label(game[-1])
        return inner if inner == outer else f"{inner} via {outer}"
    chosen = engine or innermost
    return _frame_label(chosen) if chosen is not None else "unknown work"


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
        _gc_probe (callable, optional): `(start, end) -> (seconds, longest)`
            reporting collector time inside a window on the same clock,
            injectable for testing. Defaults to `gc_policy.pause_overlap`.
        _cpu_probe (callable, optional): `() -> {thread_ident: cpu_seconds}`
            reading the CPU every live thread has used so far, injectable for
            testing. Defaults to a reader of the operating system's per-thread
            clocks, which reads nothing where there are none.

    """

    def __init__(
        self,
        threshold_ms=None,
        interval=None,
        sample_threshold_ms=None,
        _now=time.perf_counter,
        _gc_probe=None,
        _cpu_probe=None,
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
        self._gc_probe = _gc_probe or gc_policy.pause_overlap
        self._cpu_probe = _cpu_probe or _thread_cpu_seconds
        self._cpu_failed = False
        self._cpu_base = None
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
        self._stall_work = None

    @property
    def sample_interval(self) -> float:
        """float: Seconds between sampler wake-ups.

        Fine enough that a stall barely past the warning threshold is still caught
        in the act: a wake-up has to land in the sliver between the threshold and
        the end of the stall, and a 100 ms cadence missed 35% of production stalls.
        An idle wake-up is two attribute reads, so waking often is cheap.
        """
        return max(0.01, min(0.5, self.threshold_ms / 8000.0, self.sample_threshold_ms / 3000.0))

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
            self._cpu_base = None
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
        sample_interval = self.sample_interval
        while self._running:
            time.sleep(sample_interval)
            if not self._running:
                break
            now = self._now()
            self._note_stalled_work(now)
            self._sample_stall(now)

    def _cpu_now(self):
        """Read every thread's CPU time now; an empty reading if the probe fails.

        The heartbeat runs on the loop and must not fail because a diagnostic did. A
        failure is logged once and attribution stays off.
        """
        try:
            return self._cpu_probe() or {}
        except Exception:
            if not self._cpu_failed:
                self._cpu_failed = True
                logger.log_trace("reactor watchdog: the per-thread CPU probe failed")
            return {}

    def _busy_threads(self, before, after, wall):
        """Compare two CPU readings taken ``wall`` seconds apart.

        A thread that started after ``before`` was taken used all of its CPU in the
        window; one that ended before ``after`` cannot be named. The watchdog's own
        sampler thread is left out.

        Args:
            before (dict): Per-thread CPU seconds at the start of the window.
            after (dict): The same at its end.
            wall (float): Seconds between the two readings.

        Returns:
            tuple: ``(loop_cpu, busy)``. ``loop_cpu`` is the CPU the loop thread used
            (``None`` if either reading lacks it); ``busy`` is a list of ``(name,
            cpu_seconds, ident)`` for every other thread that used at least a
            quarter of the window, busiest first.
        """
        if not before or not after or wall <= 0:
            return None, []
        loop_id = self._target_thread_id
        sampler = self._sampler_thread
        skip = {loop_id, sampler.ident if sampler is not None else None}
        used = {ident: cpu - before.get(ident, 0.0) for ident, cpu in after.items()}
        loop_cpu = used.get(loop_id) if loop_id in before and loop_id in after else None
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        busy = sorted(
            (
                (cpu, ident)
                for ident, cpu in used.items()
                if ident not in skip and cpu >= _BUSY_SHARE * wall
            ),
            reverse=True,
        )
        return loop_cpu, [(names.get(ident, f"thread {ident}"), cpu, ident) for cpu, ident in busy]

    @staticmethod
    def _starved(loop_cpu, busy, wall):
        """Whether a window looks like the loop waiting on the GIL: it idled, one thread did not."""
        return (
            loop_cpu is not None
            and loop_cpu <= _LOOP_IDLE_SHARE * wall
            and bool(busy)
            and busy[0][1] >= _HOLDER_SHARE * wall
        )

    @staticmethod
    def _describe_threads(busy):
        """Name the busiest threads and where each is now, as one clause."""
        frames = sys._current_frames()
        parts = []
        for name, cpu, ident in busy[:_MAX_NAMED_THREADS]:
            frame = frames.get(ident)
            try:
                where = _work_label(frame) if frame is not None else "no live stack"
            except Exception:
                where = "a stack that could not be read"
            parts.append(f"{name} used {cpu * 1000.0:.0f}ms of CPU, now at {where}")
        return "; ".join(parts)

    def _busy_stacks(self, now):
        """Return the stacks of the other threads that used CPU since the last heartbeat.

        This runs on the sampler thread inside the stall report, so it must not raise:
        a failure here would end the sampler, and with it every later live sample.

        Args:
            now (float): The sampler's clock reading.

        Returns:
            str: Log text, or an empty string when there is no baseline, no busy thread
            or the stacks could not be read.
        """
        try:
            with self._state_lock:
                before, last = self._cpu_base, self._last_heartbeat
            if not before or last is None:
                return ""
            _loop_cpu, busy = self._busy_threads(before, self._cpu_now(), now - last)
            if not busy:
                return ""
            frames = sys._current_frames()
            parts = []
            for name, cpu, ident in busy[:_MAX_NAMED_THREADS]:
                frame = frames.get(ident)
                stack = (
                    "".join(traceback.format_stack(frame, limit=_BUSY_STACK_FRAMES))
                    if frame is not None
                    else "  (no live stack)\n"
                )
                parts.append(f"{name} used {cpu * 1000.0:.0f}ms of CPU, stack:\n{stack}")
            return (
                "Threads that used CPU while the loop waited (it may be waiting for the GIL):\n"
                + "".join(parts)
            )
        except Exception:
            return ""

    def _loop_frame(self):
        """The loop thread's current frame, or None."""
        target_id = self._target_thread_id or (
            clock.get_loop_thread_id()
            or (threading.main_thread().ident if threading.main_thread() else None)
        )
        return sys._current_frames().get(target_id) if target_id else None

    def _note_stalled_work(self, now):
        """Record where the loop thread is while it is blocked past the warning threshold."""
        with self._state_lock:
            last = self._last_heartbeat
            episode = self._heartbeat_episode
        if last is None or (now - last - self.interval) * 1000.0 <= self.threshold_ms:
            return None
        frame = self._loop_frame()
        if frame is None:
            return None
        label = _work_label(frame)
        with self._state_lock:
            if episode == self._heartbeat_episode:
                self._stall_work = (episode, label)
        return label

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
        frame = self._loop_frame()
        if frame:
            stack_str = "".join(traceback.format_stack(frame))
            logger.log_warn(
                f"Live reactor stall in progress (~{elapsed_s * 1000.0:.0f}ms blocked) "
                f"at {_work_label(frame)}! Live IO thread execution stack:\n{stack_str}"
                f"{self._busy_stacks(now)}"
            )
            return True
        return False

    def _tick(self) -> None:
        """Measure the gap since the last tick; warn if it exceeds threshold."""
        now = self._now()
        cpu = self._cpu_now()
        with self._state_lock:
            ended = self._heartbeat_episode
            work = (
                self._stall_work[1] if self._stall_work and self._stall_work[0] == ended else None
            )
            cpu_before, self._cpu_base = self._cpu_base, cpu
            self._last_heartbeat = now
            self._heartbeat_episode += 1
        if self._last is not None:
            stall_ms = (now - self._last - self.interval) * 1000.0
            if stall_ms > self.threshold_ms:
                self._report_stall(stall_ms, self._last + self.interval, now, work, cpu_before, cpu)
        self._last = now

    def _report_stall(
        self, stall_ms, window_start, window_end, work, cpu_before=None, cpu_now=None
    ) -> None:
        """Log one over-threshold stall, naming the collector or the GIL holder when one caused it.

        Args:
            stall_ms (float): How long the turn blocked, beyond its interval.
            window_start (float): Start of the blocked span on the watchdog clock.
            window_end (float): End of it.
            work (str): Where the loop was sampled during the stall, if it was.
            cpu_before (dict, optional): Per-thread CPU at the last heartbeat.
            cpu_now (dict, optional): Per-thread CPU at this one.
        """
        paused, longest = self._gc_probe(window_start, window_end)
        cause = "work" if work else "unknown"
        wall = window_end - window_start + self.interval
        try:
            loop_cpu, busy = self._busy_threads(cpu_before, cpu_now, wall)
        except Exception:
            # Attribution is a bonus; the stall must still be reported without it.
            loop_cpu, busy = None, []
        if longest is not None and paused * 1000.0 >= stall_ms * 0.5:
            cause = "gc"
            start, end, generation, collected = longest
            running = f"; running {work}" if work else ""
            message = (
                "Reactor stall: a single reactor turn blocked for ~%.0fms (threshold %.0fms) "
                "at garbage collection (generation %s, %.0fms, %s objects collected%s). "
                "The collector was running, not blocking I/O; see ENGINE_GC_POLICY."
                % (
                    stall_ms,
                    self.threshold_ms,
                    generation,
                    (end - start) * 1000.0,
                    collected,
                    running,
                )
            )
        elif self._starved(loop_cpu, busy, wall):
            cause = "gil"
            where = f" The loop was at {work}." if work else ""
            message = (
                "Reactor stall: a single reactor turn blocked for ~%.0fms (threshold %.0fms) "
                "most likely waiting for the GIL: %s.%s Keep that work short, or move it out "
                "of the game process."
                % (stall_ms, self.threshold_ms, self._describe_threads(busy), where)
            )
        else:
            share = (
                " (garbage collection took %.0fms of it)" % (paused * 1000.0)
                if longest is not None
                else ""
            )
            message = (
                "Reactor stall: a single reactor turn blocked for ~%.0fms "
                "(threshold %.0fms) at %s%s. Move blocking I/O off the reactor with "
                "evennia.utils.defer."
                % (stall_ms, self.threshold_ms, work or "work not sampled", share)
            )
        logger.log_warn(message)
        prometheus_metrics.best_effort(
            "reactor stall", prometheus_metrics.record_reactor_stall, stall_ms / 1000.0, cause
        )
