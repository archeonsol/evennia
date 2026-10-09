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
import threading
import time
import unittest
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


LOOP = 123


class _Worker:
    """A live thread parked on an event, so it has a real ident and name to report."""

    def __init__(self, name):
        self._release = threading.Event()
        self.thread = threading.Thread(target=self._release.wait, name=name, daemon=True)
        self.thread.start()

    @property
    def ident(self):
        return self.thread.ident

    def stop(self):
        self._release.set()
        self.thread.join(timeout=2)


class TestGilAttribution(SimpleTestCase):
    """A loop that waited while another thread burned CPU was starved of the GIL, not busy.

    The sampler cannot see this from the loop thread's stack: a thread waiting for the
    GIL stands at whatever frame last let go of it, usually the event loop's own idle
    wait. The CPU each thread used while the heartbeat was late says who held it.
    """

    def setUp(self):
        self.scrape = _Worker("metrics-scrape")
        self.writer = _Worker("row-writer")
        for worker in (self.scrape, self.writer):
            self.addCleanup(worker.stop)

    def _readings(self, *, loop, scrape, writer=0.0, extra=None):
        """Per-thread CPU seconds: a fixed starting reading plus what each used in the window."""
        readings = {
            LOOP: 10.0 + loop,
            self.scrape.ident: 50.0 + scrape,
            self.writer.ident: 5.0 + writer,
        }
        readings.update(extra or {})
        return readings

    def _stall(self, window, *, stall=0.50, gc_probe=None, sampled_work=None, base_extra=None):
        """Drive a primed tick, a healthy one and a stalled one; ``window`` is the last reading."""
        clock = _Clock()
        wd = ReactorStallWatchdog(
            threshold_ms=200,
            interval=0.05,
            _now=clock,
            _gc_probe=gc_probe or Mock(return_value=(0.0, None)),
            _cpu_probe=Mock(
                side_effect=[
                    self._readings(loop=0.0, scrape=0.0, extra=base_extra),
                    self._readings(loop=0.01, scrape=0.01, extra=base_extra),
                    window,
                ]
            ),
        )
        wd._target_thread_id = LOOP
        frames = {LOOP: _frames(_game_site(*sampled_work))} if sampled_work else {}
        with (
            patch.object(reactor_watchdog.sys, "_current_frames", return_value=frames),
            patch.object(reactor_watchdog.logger, "log_warn") as warn,
            patch.object(reactor_watchdog.prometheus_metrics, "record_reactor_stall") as record,
        ):
            wd._tick()
            clock.advance(0.05)
            wd._tick()
            clock.advance(0.05)
            if sampled_work:
                wd._note_stalled_work(clock() + stall)
            clock.advance(stall)
            wd._tick()
        return warn, record

    def test_a_thread_holding_the_cpu_while_the_loop_waits_is_named(self):
        warn, record = self._stall(self._readings(loop=0.02, scrape=0.52))

        message = warn.call_args.args[0]
        self.assertIn("waiting for the GIL", message)
        self.assertIn("metrics-scrape used 510ms of CPU, now at ", message)
        self.assertNotIn("Move blocking I/O", message)
        self.assertEqual(record.call_args.args[1], "gil")

    def test_the_busiest_threads_come_first_and_only_three_are_listed(self):
        extras = [_Worker(f"extra-{n}") for n in range(3)]
        for worker in extras:
            self.addCleanup(worker.stop)
        base = {worker.ident: 100.0 for worker in extras}
        busy = {worker.ident: 100.0 + 0.2 for worker in extras}

        warn, _record = self._stall(
            self._readings(loop=0.02, scrape=0.52, writer=0.30, extra=busy), base_extra=base
        )

        message = warn.call_args.args[0]
        self.assertEqual(message.count("of CPU"), 3)
        self.assertLess(message.index("metrics-scrape"), message.index("row-writer"))

    def test_the_loop_doing_the_work_itself_is_not_blamed_on_the_gil(self):
        warn, record = self._stall(self._readings(loop=0.50, scrape=0.52))

        self.assertIn("Move blocking I/O", warn.call_args.args[0])
        self.assertNotIn("GIL", warn.call_args.args[0])
        self.assertNotEqual(record.call_args.args[1], "gil")

    def test_a_loop_blocked_on_io_with_every_other_thread_idle_is_not_the_gil(self):
        warn, record = self._stall(self._readings(loop=0.02, scrape=0.02))

        self.assertIn("Move blocking I/O", warn.call_args.args[0])
        self.assertEqual(record.call_args.args[1], "unknown")

    def test_a_thread_with_a_small_share_of_the_window_is_not_the_cause(self):
        warn, record = self._stall(self._readings(loop=0.02, scrape=0.17))

        self.assertNotIn("GIL", warn.call_args.args[0])
        self.assertEqual(record.call_args.args[1], "unknown")

    def test_a_platform_without_per_thread_clocks_leaves_the_message_as_it_was(self):
        warn, record = self._stall({})

        self.assertIn("at work not sampled. Move blocking I/O", warn.call_args.args[0])
        self.assertEqual(record.call_args.args[1], "unknown")

    def test_the_collector_still_takes_precedence(self):
        probe = Mock(return_value=(0.4, (1.0, 1.4, 2, 99)))

        warn, record = self._stall(self._readings(loop=0.02, scrape=0.52), gc_probe=probe)

        self.assertIn("at garbage collection (generation 2", warn.call_args.args[0])
        self.assertEqual(record.call_args.args[1], "gc")

    def test_the_work_the_loop_was_sampled_in_is_kept(self):
        warn, record = self._stall(
            self._readings(loop=0.02, scrape=0.52),
            sampled_work=("world/rpg/courier.py", 300, "generate"),
        )

        self.assertIn("The loop was at world/rpg/courier.py:300 generate.", warn.call_args.args[0])
        self.assertEqual(record.call_args.args[1], "gil")

    def test_a_probe_that_raises_never_breaks_the_heartbeat(self):
        clock = _Clock()
        wd = ReactorStallWatchdog(
            threshold_ms=200,
            interval=0.05,
            _now=clock,
            _gc_probe=Mock(return_value=(0.0, None)),
            _cpu_probe=Mock(side_effect=RuntimeError("no clocks")),
        )
        with patch.object(reactor_watchdog.logger, "log_warn") as warn:
            wd._tick()
            clock.advance(0.35)
            wd._tick()

        self.assertIn("Reactor stall", warn.call_args.args[0])


class TestLiveSampleNamesBusyThreads(SimpleTestCase):
    """The live stack shows the loop; the threads that kept it waiting belong beside it."""

    def setUp(self):
        self.scrape = _Worker("metrics-scrape")
        self.addCleanup(self.scrape.stop)

    def _wd(self, clock, readings):
        wd = ReactorStallWatchdog(
            threshold_ms=200,
            interval=0.05,
            sample_threshold_ms=1000,
            _now=clock,
            _cpu_probe=Mock(side_effect=readings),
        )
        wd._target_thread_id = LOOP
        return wd

    def _sample(self, wd, clock):
        frames = {
            LOOP: SimpleNamespace(tag="loop"),
            self.scrape.ident: SimpleNamespace(tag="scrape"),
        }
        with (
            patch.object(reactor_watchdog.sys, "_current_frames", return_value=frames),
            patch.object(
                reactor_watchdog.traceback,
                "format_stack",
                side_effect=lambda frame, limit=None: [f"stack of {frame.tag}\n"],
            ),
            patch.object(reactor_watchdog.logger, "log_warn") as warn,
        ):
            self.assertTrue(wd._sample_stall(clock()))
        return warn.call_args.args[0]

    def test_a_thread_that_burned_the_window_is_dumped_with_its_stack(self):
        clock = _Clock()
        wd = self._wd(
            clock,
            [
                {LOOP: 1.0, self.scrape.ident: 5.0},
                {LOOP: 1.001, self.scrape.ident: 6.9},
            ],
        )
        wd._tick()
        clock.advance(2.0)

        message = self._sample(wd, clock)

        self.assertIn("stack of loop", message)
        self.assertIn("metrics-scrape used 1900ms of CPU", message)
        self.assertIn("stack of scrape", message)

    def test_idle_threads_are_not_dumped(self):
        clock = _Clock()
        wd = self._wd(
            clock,
            [
                {LOOP: 1.0, self.scrape.ident: 5.0},
                {LOOP: 1.001, self.scrape.ident: 5.01},
            ],
        )
        wd._tick()
        clock.advance(2.0)

        message = self._sample(wd, clock)

        self.assertIn("stack of loop", message)
        self.assertNotIn("metrics-scrape", message)

    def test_without_a_baseline_only_the_loop_is_dumped(self):
        clock = _Clock()
        wd = self._wd(clock, [{LOOP: 1.0, self.scrape.ident: 5.0}])
        wd._last_heartbeat = 0.0
        clock.advance(2.0)

        message = self._sample(wd, clock)

        self.assertNotIn("metrics-scrape", message)


class TestThreadCpuSeconds(SimpleTestCase):
    @unittest.skipUnless(
        hasattr(time, "pthread_getcpuclockid"), "this platform has no per-thread CPU clock"
    )
    def test_reports_this_threads_cpu_and_it_grows_with_work(self):
        me = threading.get_ident()
        before = reactor_watchdog._thread_cpu_seconds()[me]

        sum(range(3_000_000))
        after = reactor_watchdog._thread_cpu_seconds()[me]

        self.assertGreater(after, before)

    def test_a_thread_whose_clock_cannot_be_read_is_skipped(self):
        """A thread can end between being listed and being read."""
        with (
            patch.object(reactor_watchdog.time, "clock_gettime", return_value=1.0, create=True),
            patch.object(
                reactor_watchdog.time, "pthread_getcpuclockid", side_effect=OSError, create=True
            ),
        ):
            self.assertEqual(reactor_watchdog._thread_cpu_seconds(), {})

    def test_the_readable_threads_are_kept_when_one_cannot_be_read(self):
        mine = threading.get_ident()

        def clock_of(ident):
            if ident != mine:
                raise OSError("gone")
            return 7

        with (
            patch.object(reactor_watchdog.time, "clock_gettime", return_value=2.5, create=True),
            patch.object(reactor_watchdog.time, "pthread_getcpuclockid", clock_of, create=True),
        ):
            self.assertEqual(reactor_watchdog._thread_cpu_seconds(), {mine: 2.5})

    def test_a_platform_without_the_clock_reports_nothing(self):
        with patch.object(reactor_watchdog.time, "pthread_getcpuclockid", None, create=True):
            self.assertEqual(reactor_watchdog._thread_cpu_seconds(), {})
