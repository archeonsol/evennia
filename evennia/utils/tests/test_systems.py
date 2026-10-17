"""
Tests for evennia.utils.systems (the unified System Scheduler).

Cadence and driver logic are exercised by calling `SystemDriver.tick()`
directly with a manually advanced clock, so the tests are deterministic and
need no running reactor. Calendar persistence goes through ServerConfig and
needs the test database; everything else is in-memory.
"""

import asyncio
import sys
import types
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import override_settings
from twisted.internet.defer import succeed

from evennia.utils import systems
from evennia.utils.systems import (
    SystemDriver,
    SystemRegistrationError,
    all_entities,
    all_systems,
    calendar,
    every,
    every_tick,
    get_system,
    global_scope,
    online_puppets,
    register,
)
from evennia.utils.test_resources import BaseEvenniaTestCase


def _epoch(year, month, day, hour=0, minute=0, second=0):
    """Epoch seconds for a UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()


class _Clock:
    """Manually advanced wall clock (epoch seconds)."""

    def __init__(self, start=0.0):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds

    def set(self, t):
        self.t = float(t)


class _SchedulerTestMixin:
    """Shared setup: clean registry, manual clock, driver, fire recorder."""

    def setUp(self):
        super().setUp()
        systems._clear_registry()
        self.clock = _Clock()
        self.driver = SystemDriver(now=self.clock)
        self.fires = []

    def tearDown(self):
        systems._clear_registry()
        super().tearDown()

    def _recording_run(self, ctx):
        self.fires.append(ctx)


class TestCadenceConstructors(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_every_requires_positive_seconds(self):
        with self.assertRaises(ValueError):
            every(0)
        with self.assertRaises(ValueError):
            every(-5)

    def test_calendar_requires_exactly_one_spec(self):
        with self.assertRaises(ValueError):
            calendar()
        with self.assertRaises(ValueError):
            calendar(daily="12:00", weekly=(0, "12:00"))

    def test_calendar_rejects_bad_time_strings(self):
        with self.assertRaises(ValueError):
            calendar(daily="25:00")
        with self.assertRaises(ValueError):
            calendar(daily="noon")
        with self.assertRaises(ValueError):
            calendar(weekly=(7, "12:00"))
        with self.assertRaises(ValueError):
            calendar(monthly=(0, "12:00"))

    def test_describe_strings(self):
        self.assertIn("60", every(60).describe())
        self.assertIn("12:00", calendar(daily="12:00").describe())
        self.assertIn("tick", every_tick().describe())
        self.assertIn("global", global_scope().describe())
        self.assertIn("online_puppets", online_puppets().describe())
        self.assertIn("all_entities", all_entities(component="foo.Bar").describe())


class TestOccurrenceMath(_SchedulerTestMixin, BaseEvenniaTestCase):
    """_most_recent_occurrence: latest boundary at or before `now`, in UTC."""

    def test_daily_before_and_after_boundary(self):
        cad = calendar(daily="12:00")
        # 2026-03-10 10:00 -> latest noon is yesterday's
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 3, 10, 10, 0))
        self.assertEqual(occ, _epoch(2026, 3, 9, 12, 0))
        # 2026-03-10 13:00 -> today's noon
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 3, 10, 13, 0))
        self.assertEqual(occ, _epoch(2026, 3, 10, 12, 0))

    def test_weekly(self):
        # weekday 0 = Monday. 2026-03-12 is a Thursday.
        cad = calendar(weekly=(0, "08:30"))
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 3, 12, 10, 0))
        self.assertEqual(occ, _epoch(2026, 3, 9, 8, 30))
        # On Monday before the time -> previous Monday
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 3, 9, 8, 0))
        self.assertEqual(occ, _epoch(2026, 3, 2, 8, 30))

    def test_monthly(self):
        cad = calendar(monthly=(15, "00:00"))
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 3, 20))
        self.assertEqual(occ, _epoch(2026, 3, 15))
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 3, 10))
        self.assertEqual(occ, _epoch(2026, 2, 15))

    def test_monthly_clamps_short_months(self):
        # day 31 in a 28-day February clamps to Feb 28.
        cad = calendar(monthly=(31, "06:00"))
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 3, 1, 0, 0))
        self.assertEqual(occ, _epoch(2026, 2, 28, 6, 0))

    def test_monthly_wraps_year_boundary(self):
        # early January before the boundary -> December of the previous year
        cad = calendar(monthly=(15, "00:00"))
        occ = systems._most_recent_occurrence(cad, _epoch(2026, 1, 5))
        self.assertEqual(occ, _epoch(2025, 12, 15))


class TestEveryCadence(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_first_tick_primes_without_firing(self):
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        self.clock.set(100.0)
        self.driver.tick()
        self.assertEqual(len(self.fires), 0)

    def test_fires_when_elapsed_reaches_interval(self):
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        self.clock.set(100.0)
        self.driver.tick()  # prime
        for t in (101, 102, 103, 104):
            self.clock.set(t)
            self.driver.tick()
        self.assertEqual(len(self.fires), 0)
        self.clock.set(105.0)
        self.driver.tick()
        self.assertEqual(len(self.fires), 1)
        self.assertEqual(self.fires[0].now, 105.0)
        self.assertEqual(self.fires[0].dt, 5.0)

    def test_fires_repeatedly_at_boundaries(self):
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        for t in range(0, 21):
            self.clock.set(float(t))
            self.driver.tick()
        # primed at 0; fires at 5, 10, 15, 20
        self.assertEqual(len(self.fires), 4)

    def test_dt_reflects_real_elapsed_including_gaps(self):
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        self.clock.set(0.0)
        self.driver.tick()  # prime
        self.clock.set(5.0)
        self.driver.tick()  # fire, dt=5
        # simulate a 12-second stall before the next tick
        self.clock.set(17.0)
        self.driver.tick()
        self.assertEqual(len(self.fires), 2)
        self.assertEqual(self.fires[1].dt, 12.0)

    def test_late_ticks_reanchor_to_actual_fire_time(self):
        # cadence is fire-to-fire: a late fire re-anchors the next interval
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        self.clock.set(0.0)
        self.driver.tick()  # prime at 0
        self.clock.set(7.0)
        self.driver.tick()  # late fire, dt=7
        self.clock.set(11.0)
        self.driver.tick()  # only 4s since the actual fire -> no fire
        self.assertEqual(len(self.fires), 1)
        self.clock.set(12.0)
        self.driver.tick()  # 5s since 7 -> fire
        self.assertEqual(len(self.fires), 2)
        self.assertEqual(self.fires[1].dt, 5.0)


class TestEveryTickCadence(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_fires_every_tick(self):
        register(
            name="s",
            cadence=every_tick(),
            scope=global_scope(),
            run=self._recording_run,
        )
        for t in (0.0, 1.0, 2.0):
            self.clock.set(t)
            self.driver.tick()
        self.assertEqual(len(self.fires), 3)

    def test_dt_nominal_on_first_fire_then_real(self):
        register(
            name="s",
            cadence=every_tick(),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.clock.set(0.0)
        self.driver.tick()
        self.assertEqual(self.fires[0].dt, systems.TICK_INTERVAL)
        self.clock.set(3.0)  # late tick
        self.driver.tick()
        self.assertEqual(self.fires[1].dt, 3.0)

    def test_synchronous_system_does_not_spawn_async_root(self):
        register(
            name="s",
            cadence=every_tick(),
            scope=global_scope(),
            run=self._recording_run,
        )
        with patch.object(systems.clock, "run_coroutine") as spawn:
            self.driver.tick()
        spawn.assert_not_called()
        self.assertEqual(len(self.fires), 1)

    def test_awaitable_system_uses_named_database_root(self):
        async def run(ctx):
            self.fires.append(ctx)

        register(name="s", cadence=every_tick(), scope=global_scope(), run=run)

        async def scenario():
            with patch.object(
                systems.clock, "run_coroutine", wraps=systems.clock.run_coroutine
            ) as spawn:
                self.driver.tick()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            spawn.assert_called_once()
            self.assertEqual(spawn.call_args.kwargs["task_kind"], "system")

        asyncio.new_event_loop().run_until_complete(scenario())


class TestWorkloadAdmission(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_invalid_workload_class_rejected(self):
        with self.assertRaises(systems.SystemRegistrationError):
            register(
                name="bad",
                cadence=every_tick(),
                scope=global_scope(),
                run=self._recording_run,
                workload_class="nonsense",
            )

    def test_admission_budget_defers_overflow_but_keeps_due(self):
        fired = []
        for i in range(4):
            register(
                name=f"s{i}",
                cadence=every_tick(),
                scope=global_scope(),
                run=lambda ctx, i=i: fired.append(i),
            )
        with override_settings(SYSTEM_TICK_MAX_ADMISSIONS=2):
            self.clock.set(0.0)
            self.driver.tick()
            # only 2 admitted this tick
            self.assertEqual(len(fired), 2)
            # the deferred two are still due -> admitted next tick
            self.clock.set(1.0)
            self.driver.tick()
        self.assertEqual(len(fired), 4)

    def test_interactive_class_admitted_before_simulation(self):
        order = []
        register(
            name="sim",
            cadence=every_tick(),
            scope=global_scope(),
            run=lambda ctx: order.append("sim"),
            workload_class=systems.SIMULATION,
        )
        register(
            name="interactive",
            cadence=every_tick(),
            scope=global_scope(),
            run=lambda ctx: order.append("interactive"),
            workload_class=systems.INTERACTIVE,
        )
        with override_settings(SYSTEM_TICK_MAX_ADMISSIONS=1):
            self.clock.set(0.0)
            self.driver.tick()
        # interactive wins the single admission slot despite later registration
        self.assertEqual(order, ["interactive"])


class TestQuiesce(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_quiesce_cancels_overrunning_fire(self):
        async def _scenario():
            async def _long():
                await asyncio.sleep(60)

            task = asyncio.ensure_future(_long())
            self.driver._inflight.add(task)
            task.add_done_callback(self.driver._inflight.discard)
            # zero drain budget -> the still-running fire is cancelled
            await self.driver.quiesce(timeout=0)
            self.assertTrue(task.cancelled() or task.done())

        asyncio.new_event_loop().run_until_complete(_scenario())

    def test_quiesce_awaits_quick_fire(self):
        async def _scenario():
            done = []

            async def _quick():
                done.append(True)

            task = asyncio.ensure_future(_quick())
            self.driver._inflight.add(task)
            task.add_done_callback(self.driver._inflight.discard)
            await self.driver.quiesce(timeout=5)
            self.assertEqual(done, [True])
            self.assertFalse(task.cancelled())

        asyncio.new_event_loop().run_until_complete(_scenario())


class TestCalendarCadence(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_first_ever_check_primes_and_persists_without_firing(self):
        register(
            name="cal",
            cadence=calendar(daily="12:00"),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.clock.set(_epoch(2026, 3, 10, 13, 0))  # past today's boundary
        self.driver.tick()
        self.assertEqual(len(self.fires), 0)
        self.assertEqual(systems._load_last_run("cal"), _epoch(2026, 3, 10, 13, 0))

    def test_fires_once_on_boundary_crossing(self):
        register(
            name="cal",
            cadence=calendar(daily="12:00"),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.clock.set(_epoch(2026, 3, 10, 11, 59))
        self.driver.tick()  # prime
        self.clock.set(_epoch(2026, 3, 10, 12, 0, 30))
        self.driver.tick()  # crossed -> fire
        self.assertEqual(len(self.fires), 1)
        self.clock.set(_epoch(2026, 3, 10, 12, 1, 30))
        self.driver.tick()  # same boundary -> no double fire
        self.assertEqual(len(self.fires), 1)
        self.clock.set(_epoch(2026, 3, 11, 12, 0, 30))
        self.driver.tick()  # next day's boundary
        self.assertEqual(len(self.fires), 2)

    def test_missed_boundaries_during_downtime_fire_once_not_n_times(self):
        # a previous incarnation last ran on day 1 at 13:00...
        systems._store_last_run("cal", _epoch(2026, 3, 1, 13, 0))
        register(
            name="cal",
            cadence=calendar(daily="12:00"),
            scope=global_scope(),
            run=self._recording_run,
        )
        # ...the server was down for days; boot on day 4 at 09:00.
        self.clock.set(_epoch(2026, 3, 4, 9, 0))
        self.driver.tick()
        self.assertEqual(len(self.fires), 1)  # catch-up-by-one, no replay
        self.clock.set(_epoch(2026, 3, 4, 9, 1))
        self.driver.tick()
        self.assertEqual(len(self.fires), 1)  # and only once

    def test_fire_persists_last_run(self):
        register(
            name="cal",
            cadence=calendar(daily="12:00"),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.clock.set(_epoch(2026, 3, 10, 11, 0))
        self.driver.tick()  # prime
        fire_time = _epoch(2026, 3, 10, 12, 0, 30)
        self.clock.set(fire_time)
        self.driver.tick()
        self.assertEqual(systems._load_last_run("cal"), fire_time)

    def test_weekly_primes_then_fires_once_on_crossing(self):
        # weekday 0 = Monday; 2026-03-09 is a Monday.
        register(
            name="cal",
            cadence=calendar(weekly=(0, "08:00")),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.clock.set(_epoch(2026, 3, 8, 12, 0))  # Sunday: prime
        self.driver.tick()
        self.assertEqual(len(self.fires), 0)
        self.clock.set(_epoch(2026, 3, 9, 8, 0, 30))  # Monday boundary crossed
        self.driver.tick()
        self.assertEqual(len(self.fires), 1)
        self.clock.set(_epoch(2026, 3, 10, 8, 0, 30))  # Tuesday: same boundary
        self.driver.tick()
        self.assertEqual(len(self.fires), 1)

    def test_monthly_primes_then_fires_once_on_crossing(self):
        register(
            name="cal",
            cadence=calendar(monthly=(1, "00:00")),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.clock.set(_epoch(2026, 3, 31, 23, 0))  # prime late in March
        self.driver.tick()
        self.assertEqual(len(self.fires), 0)
        self.clock.set(_epoch(2026, 4, 1, 0, 0, 30))  # April boundary crossed
        self.driver.tick()
        self.assertEqual(len(self.fires), 1)
        self.clock.set(_epoch(2026, 4, 15, 0, 0))  # mid-month: no double fire
        self.driver.tick()
        self.assertEqual(len(self.fires), 1)

    def test_store_failure_leaves_state_unconsumed_and_retries(self):
        register(
            name="cal",
            cadence=calendar(daily="12:00"),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.clock.set(_epoch(2026, 3, 10, 11, 0))
        self.driver.tick()  # prime (store succeeds)
        system = get_system("cal")
        self.clock.set(_epoch(2026, 3, 10, 12, 0, 30))
        with patch.object(systems, "_store_last_run", side_effect=RuntimeError("db down")):
            self.driver.tick()  # store fails before state advances
        self.assertEqual(len(self.fires), 0)
        self.assertEqual(system.fire_count, 0)
        self.assertEqual(system.last_run, _epoch(2026, 3, 10, 11, 0))
        self.clock.set(_epoch(2026, 3, 10, 12, 1, 30))
        self.driver.tick()  # store works again -> boundary retried, fires
        self.assertEqual(len(self.fires), 1)
        self.assertEqual(system.fire_count, 1)


class TestErrorIsolation(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_failing_system_is_isolated_and_logged(self):
        def _boom(ctx):
            raise RuntimeError("kaboom")

        register(name="bad", cadence=every_tick(), scope=global_scope(), run=_boom)
        register(
            name="good",
            cadence=every_tick(),
            scope=global_scope(),
            run=self._recording_run,
        )

        # capture the live traceback log_trace would format, to prove the log
        # call happens inside the except block (not a bare str(exc))
        import traceback

        captured = {}

        def _fake_trace(msg=None, **kwargs):
            captured["tb"] = traceback.format_exc()
            captured["msg"] = msg

        with patch.object(systems.logger, "log_trace", _fake_trace):
            self.driver.tick()
            self.clock.advance(1.0)
            self.driver.tick()

        # the peer fired both ticks despite the failing sibling
        self.assertEqual(len(self.fires), 2)
        self.assertIn("bad", captured.get("msg", ""))
        self.assertIn("kaboom", captured.get("tb", ""))
        self.assertIn("_boom", captured.get("tb", ""))  # frame proves stack captured


class TestOverlapGuard(_SchedulerTestMixin, BaseEvenniaTestCase):
    """Overlap guard: a fire suspends on an ``asyncio.Future`` gate (its body or
    the entity-id query) and stays ``in_flight`` across ticks until the test
    resolves it. ``run_coroutine`` makes a real task on ``self._loop``; ``_tick``
    steps the loop so a resolve/failure unwinds the fire.
    """

    def setUp(self):
        super().setUp()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()
        asyncio.set_event_loop(None)
        super().tearDown()

    def _gate(self):
        return self._loop.create_future()

    async def _tick(self, n=3):
        for _ in range(n):
            await asyncio.sleep(0)

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def test_due_while_in_flight_skips_and_warns(self):
        pending = self._gate()
        runs = []

        def _slow(ctx):
            runs.append(ctx)
            return pending

        register(name="slow", cadence=every_tick(), scope=global_scope(), run=_slow)

        async def scenario():
            self.driver.tick()
            await self._tick()  # fire suspends on the pending body
            self.assertEqual(len(runs), 1)

            with patch.object(systems, "logger") as mock_logger:
                self.clock.advance(1.0)
                self.driver.tick()  # still in flight -> skip
                await self._tick()
            self.assertEqual(len(runs), 1)
            self.assertTrue(mock_logger.log_warn.called)

            pending.set_result(None)  # body completes
            await self._tick()
            self.clock.advance(1.0)
            self.driver.tick()
            await self._tick()
            self.assertEqual(len(runs), 2)

        self._run(scenario())

    def test_async_failure_clears_in_flight_and_logs(self):
        pending = self._gate()
        register(
            name="slow",
            cadence=every_tick(),
            scope=global_scope(),
            run=lambda ctx: pending,
        )

        async def scenario():
            self.driver.tick()
            await self._tick()  # fire suspends on the pending body
            with patch.object(systems, "logger") as mock_logger:
                pending.set_exception(RuntimeError("async kaboom"))
                await self._tick()  # failure unwinds the fire
            self.assertTrue(mock_logger.log_trace.called)
            self.assertFalse(get_system("slow").in_flight)

        self._run(scenario())

    def test_skip_escalates_to_error_after_threshold(self):
        pending = self._gate()
        register(
            name="slow",
            cadence=every_tick(),
            scope=global_scope(),
            run=lambda ctx: pending,
        )

        async def scenario():
            self.driver.tick()  # fire; body never completes
            await self._tick()
            with patch.object(systems, "logger") as mock_logger:
                for _ in range(systems._SKIP_ESCALATION_THRESHOLD):
                    self.clock.advance(1.0)
                    self.driver.tick()
            # the first skips warn; the threshold-th skip escalates to error
            self.assertEqual(
                mock_logger.log_warn.call_count, systems._SKIP_ESCALATION_THRESHOLD - 1
            )
            self.assertEqual(mock_logger.log_err.call_count, 1)
            self.assertIn("consecutive skips", str(mock_logger.log_err.call_args))

            pending.set_result(None)  # let the wedged fire finish before loop close
            await self._tick()

        self._run(scenario())

    def test_all_entities_in_flight_spans_pending_id_query(self):
        pending_ids = self._gate()
        register(
            name="sweep",
            cadence=every(1),
            scope=all_entities(component="foo.Bar"),
            run=self._recording_run,
        )

        async def scenario():
            with patch.object(systems, "_entity_ids_deferred", return_value=pending_ids):
                self.clock.set(0.0)
                self.driver.tick()  # prime
                await self._tick()
                self.clock.set(1.0)
                self.driver.tick()  # fire: id query pending, body not yet run
                await self._tick()
                self.assertTrue(get_system("sweep").in_flight)
                self.assertEqual(len(self.fires), 0)
                with patch.object(systems, "logger") as mock_logger:
                    self.clock.set(2.0)
                    self.driver.tick()  # due again while id query pending -> skip
                    await self._tick()
                self.assertTrue(mock_logger.log_warn.called)
                self.assertEqual(len(self.fires), 0)
                pending_ids.set_result([1, 2])  # query completes -> body runs
                await self._tick()
            self.assertEqual(len(self.fires), 1)
            self.assertEqual(self.fires[0].entity_ids, [1, 2])
            self.assertFalse(get_system("sweep").in_flight)

        self._run(scenario())

    def test_all_entities_id_query_failure_clears_in_flight(self):
        pending_ids = self._gate()
        register(
            name="sweep",
            cadence=every(1),
            scope=all_entities(component="foo.Bar"),
            run=self._recording_run,
        )

        async def scenario():
            with patch.object(systems, "_entity_ids_deferred", return_value=pending_ids):
                self.clock.set(0.0)
                self.driver.tick()  # prime
                await self._tick()
                self.clock.set(1.0)
                self.driver.tick()  # fire
                await self._tick()
                with patch.object(systems, "logger") as mock_logger:
                    pending_ids.set_exception(RuntimeError("query died"))
                    await self._tick()  # failure unwinds the fire
            self.assertTrue(mock_logger.log_trace.called)
            self.assertEqual(len(self.fires), 0)
            self.assertFalse(get_system("sweep").in_flight)

        self._run(scenario())

    def test_sync_raise_in_entity_selection_clears_in_flight(self):
        register(
            name="p",
            cadence=every_tick(),
            scope=online_puppets(),
            run=self._recording_run,
        )

        async def scenario():
            with (
                patch.object(
                    systems,
                    "_select_online_puppets",
                    side_effect=RuntimeError("selector died"),
                ),
                patch.object(systems, "logger") as mock_logger,
            ):
                self.driver.tick()
                await self._tick()  # selector raises inside the fire, unwinds it
            self.assertTrue(mock_logger.log_trace.called)
            self.assertFalse(get_system("p").in_flight)
            # the system recovers once the selector works again
            with patch.object(systems, "_select_online_puppets", return_value=[]):
                self.clock.advance(1.0)
                self.driver.tick()
                await self._tick()
            self.assertEqual(len(self.fires), 1)

        self._run(scenario())


class TestRegistry(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_duplicate_name_raises(self):
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        with self.assertRaises(SystemRegistrationError):
            register(
                name="s",
                cadence=every(9),
                scope=global_scope(),
                run=self._recording_run,
            )

    def test_introspection(self):
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        (system,) = all_systems()
        self.assertEqual(system.name, "s")
        self.assertIn("5", system.cadence.describe())
        self.assertIn("global", system.scope.describe())
        self.assertIsNone(system.last_run)
        self.clock.set(0.0)
        self.driver.tick()  # prime
        self.clock.set(5.0)
        self.driver.tick()  # fire
        self.assertEqual(system.last_run, 5.0)
        self.assertEqual(system.fire_count, 1)

    def test_dangerous_cell_warns_at_registration(self):
        with patch.object(systems, "logger") as mock_logger:
            register(
                name="killer",
                cadence=every_tick(),
                scope=all_entities(component="foo.Bar"),
                run=self._recording_run,
            )
        self.assertTrue(mock_logger.log_warn.called)


class TestScopeSelection(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_global_scope_gets_no_entities(self):
        register(
            name="g",
            cadence=every_tick(),
            scope=global_scope(),
            run=self._recording_run,
        )
        self.driver.tick()
        ctx = self.fires[0]
        self.assertIsNone(ctx.entities)
        self.assertIsNone(ctx.entity_ids)

    def test_select_online_puppets_real_selector(self):
        # the real selector must use the focus-stack accessor get_puppet()
        # (this fork removed the legacy session.puppet attribute) and dedup
        # multi-session puppets by pk
        import evennia

        class _FakePuppet:
            def __init__(self, pk):
                self.id = pk

        class _FakeSession:
            def __init__(self, puppet):
                self._puppet = puppet

            def get_puppet(self):
                return self._puppet

        puppet_a, puppet_b = _FakePuppet(1), _FakePuppet(2)
        sessions = [
            _FakeSession(puppet_a),
            _FakeSession(None),  # OOC session
            _FakeSession(puppet_b),
            _FakeSession(_FakePuppet(1)),  # second session on puppet pk 1
        ]

        class _FakeHandler:
            def values(self):
                return sessions

        with patch.object(evennia, "SESSION_HANDLER", _FakeHandler()):
            result = systems._select_online_puppets()
        self.assertEqual(result, [puppet_a, puppet_b])

    def test_online_puppets_uses_session_handler(self):
        puppets = ["puppet1", "puppet2"]
        register(
            name="p",
            cadence=every_tick(),
            scope=online_puppets(),
            run=self._recording_run,
        )
        with patch.object(systems, "_select_online_puppets", return_value=puppets) as sel:
            self.driver.tick()
        sel.assert_called_once()
        self.assertEqual(self.fires[0].entities, puppets)
        self.assertIsNone(self.fires[0].entity_ids)

    def test_all_entities_fetches_ids_off_reactor(self):
        register(
            name="a",
            cadence=every(5),
            scope=all_entities(component="typeclasses.objects.Thing"),
            run=self._recording_run,
        )
        with patch.object(
            systems, "_entity_ids_deferred", return_value=succeed([1, 2, 3])
        ) as fetch:
            self.clock.set(0.0)
            self.driver.tick()  # prime
            self.clock.set(5.0)
            self.driver.tick()  # fire
        fetch.assert_called_once_with("typeclasses.objects.Thing")
        self.assertEqual(self.fires[0].entity_ids, [1, 2, 3])
        self.assertIsNone(self.fires[0].entities)


class TestDiscovery(_SchedulerTestMixin, BaseEvenniaTestCase):
    def _fake_module(self, name, register_fn=None):
        mod = types.ModuleType(name)
        if register_fn is not None:
            mod.register_systems = register_fn
        sys.modules[name] = mod
        self.addCleanup(sys.modules.pop, name, None)
        return mod

    @override_settings(SYSTEM_MODULES=["no.such.module.anywhere"])
    def test_unimportable_module_is_a_loud_error(self):
        with self.assertRaises(SystemRegistrationError):
            systems.load_system_modules()

    @override_settings(SYSTEM_MODULES=["fake_systems_no_fn"])
    def test_module_without_register_systems_is_a_loud_error(self):
        self._fake_module("fake_systems_no_fn")
        with self.assertRaises(SystemRegistrationError):
            systems.load_system_modules()

    @override_settings(SYSTEM_MODULES=["fake_systems_empty"])
    def test_module_registering_nothing_is_a_loud_error(self):
        self._fake_module("fake_systems_empty", register_fn=lambda: None)
        with self.assertRaises(SystemRegistrationError):
            systems.load_system_modules()

    @override_settings(SYSTEM_MODULES=["fake_systems_good"])
    def test_registering_module_loads(self):
        def _register():
            register(
                name="from-module",
                cadence=every(5),
                scope=global_scope(),
                run=lambda ctx: None,
            )

        self._fake_module("fake_systems_good", register_fn=_register)
        systems.load_system_modules()
        self.assertIsNotNone(get_system("from-module"))

    @override_settings(SYSTEM_MODULES=[])
    def test_engine_systems_always_load(self):
        systems.load_system_modules()
        self.assertIsNotNone(get_system("flush-attributes"))

    @override_settings(SYSTEM_MODULES=[])
    def test_load_is_idempotent(self):
        # server init hooks may run more than once in a test process; a
        # repeat load rebuilds the registry instead of raising on duplicates
        systems.load_system_modules()
        systems.load_system_modules()
        self.assertIsNotNone(get_system("flush-attributes"))

    @override_settings(SYSTEM_MODULES=["fake_systems_raising"])
    def test_engine_systems_register_before_a_broken_game_module(self):
        # engine modules load first, so engine systems are registered even
        # when a game module blows up mid-load (the load still fails loud)
        def _register():
            raise RuntimeError("game module is broken")

        self._fake_module("fake_systems_raising", register_fn=_register)
        with self.assertRaises(RuntimeError):
            systems.load_system_modules()
        self.assertIsNotNone(get_system("flush-attributes"))


class TestFlushAttributesSystem(_SchedulerTestMixin, BaseEvenniaTestCase):
    def _register_flush(self):
        from evennia.server import engine_systems

        engine_systems.register_systems()
        return engine_systems

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=30)
    def test_registered_with_cadence_from_setting(self):
        self._register_flush()
        system = get_system("flush-attributes")
        self.assertIsNotNone(system)
        self.assertEqual(system.cadence.seconds, 30)
        self.assertIn("global", system.scope.describe())

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=0)
    def test_disabled_when_interval_zero(self):
        self._register_flush()
        self.assertIsNone(get_system("flush-attributes"))

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=30)
    def test_fires_on_cadence_and_flushes(self):
        engine_systems = self._register_flush()
        with (
            patch.object(
                engine_systems,
                "_flush_all_dirty_async",
                return_value={"backends": 1, "total": 2, "failed": 0},
            ) as flush,
            patch.object(engine_systems, "logger") as mock_logger,
        ):
            self.clock.set(0.0)
            self.driver.tick()  # prime
            self.clock.set(30.0)
            self.driver.tick()  # fire
        flush.assert_called_once()
        mock_logger.log_err.assert_not_called()
        mock_logger.log_trace.assert_not_called()

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=30)
    def test_flush_failure_is_isolated_and_escalates(self):
        engine_systems = self._register_flush()
        system = get_system("flush-attributes")
        ctx = systems.SystemContext(now=0.0, dt=30.0)
        with patch.object(
            engine_systems,
            "_flush_all_dirty_async",
            side_effect=RuntimeError("pg down"),
        ):
            with patch.object(engine_systems, "logger") as mock_logger:
                for _ in range(3):
                    asyncio.run(system.run(ctx))  # must not raise
        logged = " ".join(str(c) for c in mock_logger.log_err.call_args_list)
        self.assertIn("CRITICAL", logged)

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=30)
    def test_consecutive_counter_resets_on_success(self):
        engine_systems = self._register_flush()
        system = get_system("flush-attributes")
        ctx = systems.SystemContext(now=0.0, dt=30.0)
        boom = RuntimeError("pg down")
        # two failures, a success, then two failures: never 3 consecutive
        sequence = [boom, boom, {"backends": 0, "total": 0, "failed": 0}, boom, boom]
        with patch.object(engine_systems, "_flush_all_dirty_async", side_effect=sequence):
            with patch.object(engine_systems, "logger") as mock_logger:
                for _ in sequence:
                    asyncio.run(system.run(ctx))
        logged = " ".join(str(c) for c in mock_logger.log_err.call_args_list)
        self.assertNotIn("CRITICAL", logged)

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=30)
    def test_critical_is_rate_limited_past_threshold(self):
        engine_systems = self._register_flush()
        system = get_system("flush-attributes")
        ctx = systems.SystemContext(now=0.0, dt=30.0)
        fires = engine_systems._CRITICAL_THRESHOLD + engine_systems._CRITICAL_REPEAT_EVERY
        with patch.object(
            engine_systems,
            "_flush_all_dirty_async",
            side_effect=RuntimeError("pg down"),
        ):
            with patch.object(engine_systems, "logger") as mock_logger:
                for _ in range(fires):
                    asyncio.run(system.run(ctx))
        criticals = [c for c in mock_logger.log_err.call_args_list if "CRITICAL" in str(c)]
        # once at the threshold, once again after the repeat interval
        self.assertEqual(len(criticals), 2)

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=30)
    def test_driver_isolates_flush_failure_from_siblings(self):
        engine_systems = self._register_flush()
        register(
            name="sibling",
            cadence=every(30),
            scope=global_scope(),
            run=self._recording_run,
        )
        with (
            patch.object(
                engine_systems,
                "_flush_all_dirty_async",
                side_effect=RuntimeError("pg down"),
            ),
            patch.object(engine_systems, "logger"),
        ):
            self.clock.set(0.0)
            self.driver.tick()  # prime both
            self.clock.set(30.0)
            self.driver.tick()  # flush fails; sibling must still fire
        self.assertEqual(len(self.fires), 1)


class TestDriverLifecycle(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_start_runs_looping_call_at_tick_interval(self):
        with patch.object(self.driver._loop, "start") as mock_start:
            self.driver.start()
        mock_start.assert_called_once_with(systems.TICK_INTERVAL, now=False)

    def test_stop_is_safe_when_not_running(self):
        self.driver.stop()  # no raise

    def test_empty_registry_tick_is_a_noop(self):
        self.driver.tick()  # no raise, nothing registered


class _PerfClock:
    """Manually advanced stand-in for ``time.perf_counter``."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@override_settings(SYSTEM_TICK_SLICE_MS=25)
class TestSlicedTick(_SchedulerTestMixin, BaseEvenniaTestCase):
    """The loop's tick hands the reactor back between systems once a slice is spent."""

    def setUp(self):
        super().setUp()
        self.perf = _PerfClock()
        self.driver = SystemDriver(now=self.clock, perf=self.perf)
        self.events = []

    def _register(self, name, cost, workload=None):
        def run(ctx):
            self.events.append(name)
            self.fires.append(ctx)
            self.perf.advance(cost)
            if name == "A":
                # something else becomes ready while A is running
                asyncio.get_running_loop().call_soon(lambda: self.events.append("other"))

        kwargs = {"workload_class": workload} if workload else {}
        register(name=name, cadence=every_tick(), scope=global_scope(), run=run, **kwargs)

    def _run_tick(self):
        async def scenario():
            await self.driver.tick_sliced()
            await asyncio.sleep(0)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(scenario())
        finally:
            loop.close()

    def test_a_spent_slice_lets_other_work_run_before_the_next_system(self):
        for name in ("A", "B", "C"):
            self._register(name, cost=0.030)

        self._run_tick()

        self.assertEqual(self.events, ["A", "other", "B", "C"])

    def test_cheap_systems_share_one_turn(self):
        for name in ("A", "B", "C"):
            self._register(name, cost=0.001)

        self._run_tick()

        self.assertEqual(self.events, ["A", "B", "C", "other"])

    @override_settings(SYSTEM_TICK_SLICE_MS=0)
    def test_a_zero_slice_keeps_the_single_turn_tick(self):
        for name in ("A", "B", "C"):
            self._register(name, cost=0.030)

        self._run_tick()

        self.assertEqual(self.events, ["A", "B", "C", "other"])

    def test_every_system_sees_the_same_instant(self):
        for name in ("A", "B", "C"):
            self._register(name, cost=0.030)
        self.clock.set(42.0)

        self._run_tick()

        self.assertEqual([ctx.now for ctx in self.fires], [42.0, 42.0, 42.0])

    def test_priority_order_survives_slicing(self):
        self._register("A", cost=0.030, workload=systems.MAINTENANCE)
        self._register("B", cost=0.030, workload=systems.INTERACTIVE)
        self._register("C", cost=0.030, workload=systems.SIMULATION)

        self._run_tick()

        self.assertEqual([e for e in self.events if e != "other"], ["B", "C", "A"])

    def test_cancelling_between_slices_leaves_the_rest_due(self):
        for name in ("A", "B", "C"):
            self._register(name, cost=0.030)

        async def scenario():
            task = asyncio.ensure_future(self.driver.tick_sliced())
            await asyncio.sleep(0)  # A runs, the slice is spent, the tick yields
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(scenario())
        finally:
            loop.close()
        self.assertEqual([e for e in self.events if e != "other"], ["A"])

        self.driver.tick()  # the next tick still finds B and C due

        # never-fired systems rank first, so the next tick runs B and C before A again
        self.assertEqual([e for e in self.events if e != "other"], ["A", "B", "C", "A"])

    def test_the_loop_drives_the_sliced_tick(self):
        self.assertEqual(self.driver._loop._fn, self.driver.tick_sliced)

    def test_an_empty_registry_costs_nothing(self):
        self._run_tick()

        self.assertEqual(self.events, [])


@override_settings(SYSTEM_BODY_WARN_MS=100, SYSTEM_BODY_WARN_EVERY=60)
class TestBodyTiming(_SchedulerTestMixin, BaseEvenniaTestCase):
    """Every body is timed a turn at a time, counted, and named when a turn runs long."""

    def setUp(self):
        super().setUp()
        self.perf = _PerfClock()
        self.driver = SystemDriver(now=self.clock, perf=self.perf)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()
        asyncio.set_event_loop(None)
        super().tearDown()

    def _register(self, name="s", cost=0.0):
        def run(ctx):
            self.fires.append(ctx)
            self.perf.advance(cost)

        register(name=name, cadence=every_tick(), scope=global_scope(), run=run)
        return get_system(name)

    async def _settle(self):
        for _ in range(6):
            await asyncio.sleep(0)

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    # -- counting ---------------------------------------------------------------------

    def test_a_synchronous_body_is_one_timed_turn(self):
        system = self._register(cost=0.030)

        self.driver.tick()

        self.assertEqual(system.turns, 1)
        self.assertAlmostEqual(system.busy_seconds, 0.030)
        self.assertAlmostEqual(system.last_turn_seconds, 0.030)
        self.assertAlmostEqual(system.worst_turn_seconds, 0.030)

    def test_turns_add_up_and_the_worst_one_is_kept(self):
        costs = iter([0.010, 0.040, 0.020])
        system = self._register()
        system.run = lambda ctx: self.perf.advance(next(costs))

        for _ in range(3):
            self.driver.tick()

        self.assertEqual(system.turns, 3)
        self.assertAlmostEqual(system.busy_seconds, 0.070)
        self.assertAlmostEqual(system.last_turn_seconds, 0.020)
        self.assertAlmostEqual(system.worst_turn_seconds, 0.040)

    def test_a_body_that_raises_is_still_timed(self):
        system = self._register()

        def run(ctx):
            self.perf.advance(0.050)
            raise RuntimeError("boom")

        system.run = run

        with patch.object(systems, "logger"):
            self.driver.tick()

        self.assertEqual(system.turns, 1)
        self.assertAlmostEqual(system.last_turn_seconds, 0.050)
        self.assertFalse(system.in_flight)

    def test_each_system_is_counted_on_its_own(self):
        quick = self._register("quick", cost=0.001)
        slow = self._register("slow", cost=0.020)

        self.driver.tick()
        self.driver.tick()

        self.assertEqual((quick.turns, slow.turns), (2, 2))
        self.assertAlmostEqual(quick.busy_seconds, 0.002)
        self.assertAlmostEqual(slow.busy_seconds, 0.040)

    def test_an_all_entities_body_is_timed_too(self):
        def run(ctx):
            self.perf.advance(0.015)

        register(name="all", cadence=every(5), scope=all_entities(component="x.Y"), run=run)
        system = get_system("all")
        ids = self._loop.create_future()
        ids.set_result([1])

        async def scenario():
            with patch.object(systems, "_entity_ids_deferred", return_value=ids):
                self.clock.set(0.0)
                self.driver.tick()  # prime
                self.clock.set(5.0)
                self.driver.tick()  # fire
                await self._settle()

        self._run(scenario())

        self.assertEqual(system.turns, 1)
        self.assertAlmostEqual(system.busy_seconds, 0.015)

    # -- an async body ----------------------------------------------------------------

    def test_an_async_body_is_timed_a_turn_at_a_time_and_not_while_it_waits(self):
        gate = self._loop.create_future()

        async def run(ctx):
            self.perf.advance(0.010)
            await gate
            self.perf.advance(0.030)

        register(name="a", cadence=every_tick(), scope=global_scope(), run=run)
        system = get_system("a")

        async def scenario():
            self.driver.tick()
            await self._settle()
            self.assertEqual(system.turns, 1)
            self.perf.advance(5.0)  # suspended: that time is not the reactor's
            gate.set_result(None)
            await self._settle()

        self._run(scenario())

        self.assertEqual(system.turns, 2)
        self.assertAlmostEqual(system.busy_seconds, 0.040)
        self.assertAlmostEqual(system.worst_turn_seconds, 0.030)
        self.assertFalse(system.in_flight)

    def test_an_async_body_that_raises_after_an_await_is_timed_and_logged(self):
        async def run(ctx):
            self.perf.advance(0.020)
            await asyncio.sleep(0)
            self.perf.advance(0.040)
            raise RuntimeError("boom")

        register(name="a", cadence=every_tick(), scope=global_scope(), run=run)
        system = get_system("a")

        async def scenario():
            with patch.object(systems, "logger") as log:
                self.driver.tick()
                await self._settle()
            return log

        log = self._run(scenario())

        self.assertEqual(system.turns, 2)
        self.assertAlmostEqual(system.last_turn_seconds, 0.040)
        self.assertTrue(log.log_trace.called)
        self.assertFalse(system.in_flight)

    def test_a_cancelled_async_body_sees_the_cancellation_and_is_timed(self):
        seen = []
        never = self._loop.create_future()

        async def run(ctx):
            self.perf.advance(0.010)
            try:
                await never
            except asyncio.CancelledError:
                seen.append("cancelled")
                self.perf.advance(0.020)
                raise

        register(name="a", cadence=every_tick(), scope=global_scope(), run=run)
        system = get_system("a")

        async def scenario():
            self.driver.tick()
            await self._settle()
            for task in list(self.driver._inflight):
                task.cancel()
            await self._settle()

        self._run(scenario())

        self.assertEqual(seen, ["cancelled"])
        self.assertEqual(system.turns, 2)
        self.assertAlmostEqual(system.busy_seconds, 0.030)
        self.assertFalse(system.in_flight)

    def test_a_body_that_returns_a_deferred_is_timed_for_the_call_only(self):
        def run(ctx):
            self.perf.advance(0.012)
            return succeed(None)

        register(name="d", cadence=every_tick(), scope=global_scope(), run=run)
        system = get_system("d")

        self._run(self._tick_and_settle())

        self.assertEqual(system.turns, 1)
        self.assertAlmostEqual(system.busy_seconds, 0.012)

    async def _tick_and_settle(self):
        self.driver.tick()
        await self._settle()

    def test_a_plain_function_that_hands_back_a_coroutine_counts_its_own_work(self):
        async def later():
            self.perf.advance(0.004)

        def run(ctx):
            self.perf.advance(0.025)
            return later()

        register(name="p", cadence=every_tick(), scope=global_scope(), run=run)
        system = get_system("p")

        self._run(self._tick_and_settle())

        self.assertEqual(system.turns, 2)
        self.assertAlmostEqual(system.busy_seconds, 0.029)

    # -- who is running ---------------------------------------------------------------

    def test_the_running_body_is_named_while_it_runs_and_not_after(self):
        seen = []
        system = self._register("who")
        system.run = lambda ctx: seen.append(systems.running_body())

        self.assertIsNone(systems.running_body())
        self.driver.tick()

        name, seconds = seen[0]
        self.assertEqual(name, "who")
        self.assertGreaterEqual(seconds, 0.0)
        self.assertIsNone(systems.running_body())

    def test_the_running_body_says_how_long_its_turn_has_gone_on(self):
        with (
            patch.object(systems, "_RUNNING", ("who", 10.0)),
            patch.object(systems.time, "perf_counter", return_value=10.5),
        ):
            self.assertEqual(systems.running_body(), ("who", 0.5))

    def test_nothing_is_running_after_a_body_raises(self):
        system = self._register("who")

        def run(ctx):
            raise RuntimeError("boom")

        system.run = run

        with patch.object(systems, "logger"):
            self.driver.tick()

        self.assertIsNone(systems.running_body())

    def test_an_async_body_is_named_in_each_turn_and_not_while_it_waits(self):
        gate = self._loop.create_future()
        seen = []

        async def run(ctx):
            seen.append(systems.running_body())
            await gate
            seen.append(systems.running_body())

        register(name="a", cadence=every_tick(), scope=global_scope(), run=run)

        async def scenario():
            self.driver.tick()
            await self._settle()
            waiting = systems.running_body()
            gate.set_result(None)
            await self._settle()
            return waiting

        waiting = self._run(scenario())

        self.assertIsNone(waiting)
        self.assertEqual([entry[0] for entry in seen], ["a", "a"])
        self.assertIsNone(systems.running_body())

    # -- the line in the log ----------------------------------------------------------

    def test_a_turn_at_the_limit_is_named(self):
        system = self._register("slow", cost=0.100)

        with patch.object(systems, "logger") as log:
            self.driver.tick()

        log.log_warn.assert_called_once()
        text = log.log_warn.call_args.args[0]
        self.assertIn("System 'slow' held the reactor for 100ms in one turn", text)
        self.assertIn("limit 100ms", text)
        self.assertEqual(system.slow_turns, 1)

    def test_a_turn_under_the_limit_is_silent(self):
        system = self._register("quick", cost=0.099)

        with patch.object(systems, "logger") as log:
            self.driver.tick()

        log.log_warn.assert_not_called()
        self.assertEqual(system.slow_turns, 0)

    def test_the_line_is_held_back_for_a_minute_and_says_how_many_went_over(self):
        system = self._register("slow", cost=0.100)

        with patch.object(systems, "logger") as log:
            self.driver.tick()
            self.driver.tick()
            self.driver.tick()
            self.assertEqual(log.log_warn.call_count, 1)
            self.perf.advance(60.0)
            self.driver.tick()

        self.assertEqual(log.log_warn.call_count, 2)
        text = log.log_warn.call_args.args[0]
        self.assertIn("3 turns went over the limit since the last line", text)
        self.assertIn("the longest 100ms", text)
        self.assertEqual(system.slow_turns, 4)

    def test_the_first_line_does_not_count_turns_before_it(self):
        self._register("slow", cost=0.100)

        with patch.object(systems, "logger") as log:
            self.driver.tick()

        self.assertNotIn("went over", log.log_warn.call_args.args[0])

    def test_each_system_has_its_own_line(self):
        self._register("one", cost=0.100)
        self._register("two", cost=0.100)

        with patch.object(systems, "logger") as log:
            self.driver.tick()

        named = sorted(call.args[0].split("'")[1] for call in log.log_warn.call_args_list)
        self.assertEqual(named, ["one", "two"])

    @override_settings(SYSTEM_BODY_WARN_MS=0)
    def test_a_zero_limit_turns_the_line_off(self):
        system = self._register("slow", cost=5.0)

        with patch.object(systems, "logger") as log:
            self.driver.tick()

        log.log_warn.assert_not_called()
        self.assertEqual(system.slow_turns, 0)
        self.assertAlmostEqual(system.worst_turn_seconds, 5.0)

    @override_settings(SYSTEM_BODY_WARN_EVERY=0)
    def test_a_zero_gap_names_every_slow_turn(self):
        self._register("slow", cost=0.100)

        with patch.object(systems, "logger") as log:
            self.driver.tick()
            self.driver.tick()

        self.assertEqual(log.log_warn.call_count, 2)

    # -- Prometheus and faults --------------------------------------------------------

    def test_every_turn_is_handed_to_the_histogram(self):
        self._register("measured", cost=0.030)

        with patch.object(systems.prometheus_metrics, "record_system_body") as record:
            self.driver.tick()

        record.assert_called_once_with("measured", 0.030)

    def test_the_histogram_counts_each_turn_by_system(self):
        try:
            from prometheus_client import REGISTRY
        except ImportError:
            self.skipTest("prometheus_client is not installed")
        labels = {"system": "histogrammed"}

        def sample(kind):
            return REGISTRY.get_sample_value(f"evennia_system_body_seconds_{kind}", labels) or 0.0

        self._register("histogrammed", cost=0.030)
        count_before, total_before = sample("count"), sample("sum")

        self.driver.tick()
        self.driver.tick()

        self.assertEqual(sample("count") - count_before, 2)
        self.assertAlmostEqual(sample("sum") - total_before, 0.060)

    def test_a_fault_in_the_timing_never_reaches_the_body_and_is_logged_once(self):
        self._register("steady", cost=0.001)

        with (
            patch.object(
                systems.prometheus_metrics, "best_effort", side_effect=RuntimeError("telemetry")
            ),
            patch.object(systems, "logger") as log,
        ):
            self.driver.tick()
            self.driver.tick()

        self.assertEqual(len(self.fires), 2)
        self.assertEqual(log.log_trace.call_count, 1)
