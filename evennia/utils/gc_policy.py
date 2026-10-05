"""
Managed garbage collection for the game loop.

CPython's automatic collector is built for programs that may pause anywhere. A
game server may not: every connected player waits behind one pause. On the
production heap (several million tracked objects, mostly idmapper instances and
the JSONB documents behind them) the Python 3.14 incremental collector marks the
whole heap in one uninterrupted step at the start of each old-generation cycle,
about every six minutes, for 0.5 to 1.3 seconds, and scans a further increment
of the old generation on most other collections.

Those pauses do buy something: they are the only thing that frees a cycle which
outlived a young collection, and the largest source of such cycles is the
idmapper itself. An evicted instance and its handlers refer to one another, so
reference counting never frees it, and on production each cache sweep leaves
dozens of container objects behind per evicted instance. A policy that only
collects the young generation therefore leaks every eviction. (The first version
of this module did exactly that and the process grew by about 5 MB a minute.)

The ``managed`` policy keeps what the collector is good at and drops what costs:

* **Boot.** One full collection runs after startup, while nobody is waiting, and
  the survivors are frozen. Frozen objects (modules, registries, the code) are
  never scanned again, which also makes every later full collection cheaper. A
  frozen cycle is never freed either, so an instance that was cached at boot and
  is evicted later stays until the next restart.
* **Steady state.** Automatic collection is disabled. A timer on the game loop
  collects the young generation once enough new objects have accumulated. A young
  pass touches only objects allocated since the last one, so it costs about
  35 ns per object and cannot trigger an old-generation mark. Because the timer
  fires between turns rather than at an allocation in the middle of one, most of
  a turn's temporary cycles are already dead when it runs.
* **Reclaim.** A full collection of the unfrozen heap frees what young passes
  cannot. It costs a fraction of a second (the unfrozen live objects, not the
  garbage, set the price), so it runs when the interpreter's object heap has grown
  by ``ENGINE_GC_RECLAIM_GROWTH_PERCENT`` since the last one, and as a backstop from
  the system scheduler (:func:`deep_clean_if_due`). Growth is counted in allocated
  blocks, not process size: freed memory goes back to the allocator's pool, not to
  the operating system, so the process stops growing while garbage fills the pool
  and a trigger on its size would wait ever longer. A clean that finds little (the
  growth was live data, such as a cache filling) doubles the growth the next one
  waits for, so it does not cost a pause every few minutes. Each reports what it
  found and the process size before and after, so an operator can see whether the
  trigger is right. Code that knows it just dropped a great deal can ask for one
  with :func:`request_reclaim`.
* **Refreeze.** Optionally (``ENGINE_GC_REFREEZE``) the survivors of each full
  collection are frozen too, so the next one marks only what was allocated since
  and costs a few percent of the first. What that gives up is the freeing of a
  cycle that was alive when frozen and dies later, so the two backstop collections
  thaw the permanent generation, collect all of it, and freeze again.

Under Python 3.14 a collection's ``generation`` is 0 for a young-only pass (what
``gc.collect(0)`` runs), 1 for an increment (the young generation plus a slice of
the old one, which is what the automatic collector runs) and 2 for a full pass.
``python_gc_*`` metrics use the same numbers.

Every collection, in either policy, is timed. Pauses feed the
``evennia_gc_pause_seconds`` histogram, long ones are logged, and the reactor
stall watchdog asks :func:`pause_overlap` so a stall caused by the collector is
named as such rather than blamed on whatever code happened to be running.
"""

from __future__ import annotations

import gc
import sys
import time
from collections import deque
from dataclasses import dataclass

from django.conf import settings

from evennia.server import prometheus_metrics
from evennia.utils import clock, logger, process_memory

#: Seam for tests: the monotonic clock collection timestamps are taken from.
#: The reactor stall watchdog uses the same clock, so windows compare directly.
_now = time.perf_counter

_POLICIES = ("managed", "default")

#: Collections at least this long (seconds) are remembered for stall attribution.
_REMEMBER_PAUSE_SECONDS = 0.005
#: Long-pause warnings are spaced at least this far apart (seconds).
_WARN_EVERY_SECONDS = 10.0
#: How often the growth trigger counts the interpreter's allocated blocks (seconds).
#: The count visits every allocator pool, about a millisecond for a 1.5 GB heap.
_GROWTH_CHECK_SECONDS = 15.0
#: A full collection that frees fewer unreachable objects than this found little: the
#: growth that asked for it was not garbage.
_USEFUL_OBJECTS = 20_000
#: The growth trigger waits at most this many times ``ENGINE_GC_RECLAIM_GROWTH_PERCENT``.
_MAX_GROWTH_FACTOR = 8
#: Deep cleans that thaw the permanent generation first when ``ENGINE_GC_REFREEZE`` is on.
_THAW_REASONS = frozenset({"scheduled", "overdue"})
#: Reasons that label ``evennia_gc_reclaim_total``; anything else is ``manual``.
_RECLAIM_REASONS = frozenset({"growth", "requested", "scheduled", "overdue", "manual"})

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
        self.last_reclaim = None
        self.pending = None
        self.blocks_floor = None
        self.growth_factor = 1
        self.next_growth_check = 0.0
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
            _reclaim_if_needed()
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
        _state.last_reclaim = _now()
        _state.blocks_floor = _allocated_blocks()
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


def _rss_mb():
    """Resident memory of this process in MB, or ``None``. A seam for tests."""
    return process_memory.current_rss_mb()


def _allocated_blocks():
    """Memory blocks the interpreter has allocated right now. A seam for tests."""
    return sys.getallocatedblocks()


def deep_clean(reason="manual"):
    """Run one full collection and report what it cost.

    A full collection marks the whole unfrozen heap, so it pauses the process for
    a noticeable fraction of a second. Every full collection the policy runs comes
    through here, which is how the growth trigger knows where the process stood
    afterwards and how much the collection was worth.

    Args:
        reason (str, optional): Why it ran (``sweep``, ``growth``, ``scheduled``,
            ``overdue`` or ``manual``), for the log line and the metrics.

    Returns:
        DeepClean: How many objects were freed and how long it took.
    """
    refreeze = bool(getattr(settings, "ENGINE_GC_REFREEZE", False))
    # Only the backstops look at the frozen heap, so a cycle frozen alive and dead
    # since is freed at the next one rather than never.
    thaw = refreeze and reason in _THAW_REASONS
    rss_before, blocks_before = _rss_mb(), _allocated_blocks()
    started = _now()
    if thaw:
        gc.unfreeze()
    collected = _intentional_collect()
    if refreeze:
        gc.freeze()
    ended = _now()
    seconds = ended - started
    _state.last_deep_clean = time.time()
    _state.last_reclaim = ended
    rss_after, blocks_after = _rss_mb(), _allocated_blocks()
    _state.blocks_floor = blocks_after
    if collected < _USEFUL_OBJECTS:
        _state.growth_factor = min(_state.growth_factor * 2, _MAX_GROWTH_FACTOR)
    else:
        _state.growth_factor = 1
    size = (
        f", process {rss_before:.0f} -> {rss_after:.0f} MB"
        if rss_before is not None and rss_after is not None
        else ""
    )
    frozen = f", {gc.get_freeze_count()} frozen" if refreeze else ""
    logger.log_info(
        f"gc deep clean ({reason}): {collected} unreachable objects collected "
        f"in {seconds * 1000:.0f}ms (allocated blocks {blocks_before} -> {blocks_after}"
        f"{size}{frozen})."
    )
    label = reason if reason in _RECLAIM_REASONS else "manual"
    prometheus_metrics.best_effort(
        "gc reclaim", prometheus_metrics.record_gc_reclaim, label, collected
    )
    return DeepClean(collected, seconds)


def request_reclaim(reason="requested"):
    """Ask for a full collection at the game loop's next free moment.

    For code that knows it just dropped a great deal of cyclic garbage. Nothing
    runs inside this call. The loop timer that drives young collections notices the
    request and runs :func:`deep_clean` between turns, no sooner than
    ``ENGINE_GC_RECLAIM_MIN_INTERVAL`` seconds after the previous full collection,
    so a burst of requests costs one pause. Safe to call from any thread.

    Args:
        reason (str, optional): Why, for the metrics label.

    Returns:
        bool: Whether the request was taken. ``False`` unless the managed policy is
        in force: under ``default`` the interpreter's own collector finds the
        garbage and there is nothing to ask for.
    """
    if not _state.managed:
        return False
    if _state.pending is None:
        _state.pending = reason if reason in _RECLAIM_REASONS else "requested"
    return True


def _reclaim_min_interval():
    return float(getattr(settings, "ENGINE_GC_RECLAIM_MIN_INTERVAL", 300) or 0)


def _grown_enough():
    """Whether the object heap has grown enough since the last full collection to ask for one."""
    percent = float(getattr(settings, "ENGINE_GC_RECLAIM_GROWTH_PERCENT", 15) or 0)
    if percent <= 0 or not _state.blocks_floor:
        return False
    limit = _state.blocks_floor * (1.0 + percent * _state.growth_factor / 100.0)
    return _allocated_blocks() >= limit


def _reclaim_if_needed():
    """Run the full collection a request or the growth trigger calls for, if spacing allows.

    Called from the loop timer after each young check, so it runs between turns.
    """
    if not _state.managed:
        return
    now = _now()
    last = _state.last_reclaim
    spaced = last is None or now - last >= _reclaim_min_interval()
    if _state.pending is not None:
        if spaced:
            reason, _state.pending = _state.pending, None
            deep_clean(reason)
        return
    if now < _state.next_growth_check:
        return
    _state.next_growth_check = now + _GROWTH_CHECK_SECONDS
    if spaced and _grown_enough():
        deep_clean("growth")


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
