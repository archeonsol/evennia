"""
Managed garbage collection for the game loop.

CPython's automatic collector is built for programs that may pause anywhere. A
game server may not: every connected player waits behind one pause. On the
production heap (several million tracked objects, mostly idmapper instances and
the JSONB documents behind them) the Python 3.14 incremental collector marks the
whole heap in one uninterrupted step at the start of each old-generation cycle,
about every six minutes, for 0.5 to 1.3 seconds, and scans a further increment
of the old generation on most other collections. Those passes found almost
nothing: 7.5 thousand objects in two hours, against 4.6 million from young
collections. The pauses bought no memory.

The ``managed`` policy keeps what the collector is good at and drops what costs:

* **Boot.** One full collection runs after startup, while nobody is waiting, and
  the survivors are frozen. Frozen objects (modules, registries, the code) are
  never scanned again, which also makes every later full collection cheaper.
* **Steady state.** Automatic collection is disabled. A timer on the game loop
  collects the young generation once enough new objects have accumulated. A young
  pass touches only objects allocated since the last one, so it costs about
  35 ns per object and cannot trigger an old-generation mark. Because the timer
  fires between turns rather than at an allocation in the middle of one, most of
  a turn's temporary cycles are already dead when it runs.
* **Deep clean.** A full collection reclaims cycles that outlived a young pass.
  It runs from the system scheduler when no one is connected (or, after
  ``ENGINE_GC_DEEP_CLEAN_MAX_DEFER``, regardless) and reports how much it found,
  so an operator can see whether the interval is right.

Every collection, in either policy, is timed. Pauses feed the
``evennia_gc_pause_seconds`` histogram, long ones are logged, and the reactor
stall watchdog asks :func:`pause_overlap` so a stall caused by the collector is
named as such rather than blamed on whatever code happened to be running.
"""

from __future__ import annotations

import gc
import time
from collections import deque
from dataclasses import dataclass

from django.conf import settings

from evennia.server import prometheus_metrics
from evennia.utils import clock, logger

#: Seam for tests: the monotonic clock collection timestamps are taken from.
#: The reactor stall watchdog uses the same clock, so windows compare directly.
_now = time.perf_counter

_POLICIES = ("managed", "default")

#: Collections at least this long (seconds) are remembered for stall attribution.
_REMEMBER_PAUSE_SECONDS = 0.005
#: Long-pause warnings are spaced at least this far apart (seconds).
_WARN_EVERY_SECONDS = 10.0

_RECENT_PAUSES: deque = deque(maxlen=128)


class _State:
    """Process-wide policy state. Only the game loop thread mutates the driver."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Return to the state of a process that never applied a policy."""
        self.managed = False
        self.driver = None
        self.booted = False
        self.was_enabled = True
        self.last_deep_clean = 0.0
        self.started = None
        self.last_warn = None
        self.suppressed = 0
        self.intentional = False


_state = _State()


@dataclass(frozen=True)
class GCStatus:
    """What :func:`apply_gc_policy` put in force.

    Attributes:
        policy (str): ``"managed"`` or ``"default"``. Reports ``"default"`` when
            ``managed`` was requested but could not be applied.
        young_threshold (int): Young-generation count that triggers a collection.
        interval (float): Seconds between checks of that count.
        frozen (bool): Whether the boot heap was frozen.
    """

    policy: str
    young_threshold: int = 0
    interval: float = 0.0
    frozen: bool = False


@dataclass(frozen=True)
class DeepClean:
    """Result of one full collection.

    Attributes:
        collected (int): Unreachable objects the collector freed.
        seconds (float): Wall time the collection took.
    """

    collected: int
    seconds: float


def _snapshot():
    """Copy the pause log, tolerating a collection that appends mid-copy."""
    for _ in range(3):
        try:
            return tuple(_RECENT_PAUSES)
        except RuntimeError:
            continue
    return ()


def recent_pauses():
    """Return the remembered long collections, oldest first.

    Returns:
        list[tuple]: ``(start, end, generation, collected)`` per collection, with
        times on the ``perf_counter`` clock.
    """
    return list(_snapshot())


def pause_overlap(start, end):
    """Measure how much of a time window was spent inside the collector.

    Args:
        start (float): Window start on the ``perf_counter`` clock.
        end (float): Window end on the same clock.

    Returns:
        tuple: ``(seconds, longest)``. ``seconds`` is the total collector time
        inside the window; ``longest`` is the ``(start, end, generation,
        collected)`` of the longest overlapping collection, or ``None``.
    """
    total = 0.0
    longest = None
    for pause in _snapshot():
        overlap = min(end, pause[1]) - max(start, pause[0])
        if overlap <= 0:
            continue
        total += overlap
        if longest is None or pause[1] - pause[0] > longest[1] - longest[0]:
            longest = pause
    return total, longest


def _warn_if_slow(seconds, generation, collected, now):
    """Log a collection that paused the process past the configured threshold."""
    threshold = float(getattr(settings, "ENGINE_GC_PAUSE_WARN_MS", 50) or 0) / 1000.0
    if threshold <= 0 or seconds < threshold:
        return
    if _state.last_warn is not None and now - _state.last_warn < _WARN_EVERY_SECONDS:
        _state.suppressed += 1
        return
    more = f" ({_state.suppressed} more since the last report)" if _state.suppressed else ""
    _state.suppressed = 0
    _state.last_warn = now
    logger.log_warn(
        f"Garbage collection paused the process for {seconds * 1000:.0f}ms "
        f"(generation {generation}, {collected} objects collected){more}. "
        "Every player waits behind a pause; see ENGINE_GC_POLICY."
    )


def _on_collection(phase, info):
    """``gc.callbacks`` entry: time every collection and report the long ones."""
    try:
        if phase == "start":
            _state.started = _now()
            return
        started, _state.started = _state.started, None
        if started is None:
            return
        end = _now()
        seconds = end - started
        generation = info.get("generation", -1)
        collected = info.get("collected", 0)
        prometheus_metrics.best_effort(
            "gc pause", prometheus_metrics.record_gc_pause, generation, seconds
        )
        if seconds >= _REMEMBER_PAUSE_SECONDS:
            _RECENT_PAUSES.append((started, end, generation, collected))
        if not _state.intentional:
            _warn_if_slow(seconds, generation, collected, end)
    except Exception:
        logger.log_trace("gc_policy: collection telemetry failed")


def install_gc_telemetry():
    """Start timing every collection. Safe to call repeatedly."""
    if _on_collection not in gc.callbacks:
        gc.callbacks.append(_on_collection)


def uninstall_gc_telemetry():
    """Stop timing collections."""
    while _on_collection in gc.callbacks:
        gc.callbacks.remove(_on_collection)


class _YoungCollector:
    """Collects the young generation from a loop timer.

    Args:
        loop: Event loop providing ``call_later``.
        interval (float): Seconds between checks.
        threshold (int): Young-generation count that triggers a collection.
    """

    def __init__(self, loop, interval, threshold):
        self.loop = loop
        self.interval = interval
        self.threshold = threshold
        self.handle = None
        self.running = False

    def start(self):
        """Begin checking."""
        self.running = True
        self._arm()

    def stop(self):
        """Stop checking and cancel the pending timer."""
        self.running = False
        handle, self.handle = self.handle, None
        if handle is not None:
            handle.cancel()

    def _arm(self):
        self.handle = self.loop.call_later(self.interval, self._tick)

    def _tick(self):
        """Collect if enough has accumulated, then schedule the next check."""
        if not self.running:
            return
        try:
            if gc.get_count()[0] >= self.threshold:
                gc.collect(0)
        except Exception:
            logger.log_trace("gc_policy: young-generation collection failed")
        finally:
            if self.running:
                self._arm()


def _stop_driver():
    """Cancel the young-generation timer without touching the collector."""
    driver, _state.driver = _state.driver, None
    if driver is not None:
        driver.stop()


def _intentional_collect():
    """Run a full collection the caller chose, without the slow-pause warning.

    The caller reports the cost itself; a second warning about the same pause
    would only read as a fault.
    """
    _state.intentional = True
    try:
        return gc.collect()
    finally:
        _state.intentional = False


def _manage(loop):
    """Settle the boot heap if needed and hand collection to a loop timer."""
    freeze = bool(getattr(settings, "ENGINE_GC_FREEZE_AT_START", True))
    interval = max(0.005, float(getattr(settings, "ENGINE_GC_INTERVAL_MS", 50)) / 1000.0)
    threshold = max(1, int(getattr(settings, "ENGINE_GC_YOUNG_THRESHOLD", 2000)))
    _stop_driver()
    if not _state.booted:
        started = _now()
        collected = _intentional_collect()
        if freeze:
            gc.freeze()
        _state.booted = True
        _state.last_deep_clean = time.time()
        logger.log_info(
            f"gc: boot heap settled in {(_now() - started) * 1000:.0f}ms "
            f"({collected} unreachable objects collected{', survivors frozen' if freeze else ''})."
        )
    if not _state.managed:
        _state.was_enabled = gc.isenabled()
    gc.disable()
    _state.managed = True
    _state.driver = _YoungCollector(loop, interval, threshold)
    _state.driver.start()
    return GCStatus("managed", threshold, interval, freeze)


def apply_gc_policy():
    """Put the configured collection policy in force.

    Reads ``ENGINE_GC_POLICY`` (``"managed"`` or ``"default"``). Collection timing
    is installed under either policy. ``managed`` needs the bound game loop; with
    none, the automatic collector stays on rather than leaving a process with no
    collection at all.

    Returns:
        GCStatus: The policy now in force.
    """
    install_gc_telemetry()
    policy = str(getattr(settings, "ENGINE_GC_POLICY", "managed")).lower()
    if policy not in _POLICIES:
        logger.log_warn(
            f"Unknown ENGINE_GC_POLICY {policy!r}; using 'default'. Choose one of {_POLICIES}."
        )
        policy = "default"
    if policy == "default":
        stop_gc_policy()
        return GCStatus("default")
    loop = clock.get_bound_loop()
    if loop is None:
        logger.log_warn(
            "ENGINE_GC_POLICY is 'managed' but no event loop is bound; "
            "leaving the automatic collector on."
        )
        return GCStatus("default")
    return _manage(loop)


def stop_gc_policy():
    """Cancel the young-generation timer and restore the automatic collector."""
    _stop_driver()
    if _state.managed:
        _state.managed = False
        if _state.was_enabled:
            gc.enable()


def deep_clean(reason="manual"):
    """Run one full collection and report what it cost.

    A full collection marks the whole unfrozen heap, so it pauses the process for
    a noticeable fraction of a second. Call it when no one is waiting.

    Args:
        reason (str, optional): Free text for the log line.

    Returns:
        DeepClean: How many objects were freed and how long it took.
    """
    started = _now()
    collected = _intentional_collect()
    seconds = _now() - started
    _state.last_deep_clean = time.time()
    logger.log_info(
        f"gc deep clean ({reason}): {collected} unreachable objects collected "
        f"in {seconds * 1000:.0f}ms."
    )
    return DeepClean(collected, seconds)


def _connected_sessions():
    """Count the sessions attached to this server."""
    from evennia.server import sessionhandler

    handler = sessionhandler.SESSION_HANDLER
    return len(handler) if handler is not None else 0


def deep_clean_if_due(now=None, session_count=None):
    """Run a deep clean when the schedule and the player count allow it.

    A clean is due after ``ENGINE_GC_DEEP_CLEAN_INTERVAL`` seconds. It then waits
    for a moment when no one is connected, but never longer than
    ``ENGINE_GC_DEEP_CLEAN_MAX_DEFER`` seconds after the last one. A policy other
    than ``managed`` never needs it: the automatic collector does the work.

    Args:
        now (float, optional): Wall-clock seconds. Defaults to ``time.time()``.
        session_count (callable, optional): Returns the connected-session count.
            Defaults to the server session handler.

    Returns:
        bool: Whether a deep clean ran.
    """
    if not _state.managed:
        return False
    interval = float(getattr(settings, "ENGINE_GC_DEEP_CLEAN_INTERVAL", 86400) or 0)
    if interval <= 0:
        return False
    now = time.time() if now is None else now
    age = now - _state.last_deep_clean
    if age < interval:
        return False
    max_defer = float(getattr(settings, "ENGINE_GC_DEEP_CLEAN_MAX_DEFER", 259200) or 0)
    quiet = (session_count or _connected_sessions)() == 0
    if not quiet and age < max(max_defer, interval):
        return False
    deep_clean("scheduled" if quiet else "overdue")
    _state.last_deep_clean = now
    return True


def _reset_for_tests():
    """Drop every trace of a previously applied policy. Test isolation only."""
    _stop_driver()
    uninstall_gc_telemetry()
    _RECENT_PAUSES.clear()
    _state.reset()
