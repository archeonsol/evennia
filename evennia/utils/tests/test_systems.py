"""
Tests for evennia.utils.systems (the unified System Scheduler).

Cadence and driver logic are exercised by calling `SystemDriver.tick()`
directly with a manually advanced clock, so the tests are deterministic and
need no running reactor. Calendar persistence goes through ServerConfig and
needs the test database; everything else is in-memory.
"""

import sys
import types
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import override_settings
from twisted.internet.defer import Deferred, succeed

from evennia.utils import systems
from evennia.utils.systems import (SystemDriver, SystemRegistrationError,
                                   all_entities, all_systems, calendar, every,
                                   every_tick, get_system, global_scope,
                                   online_puppets, register)
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


class TestEveryTickCadence(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_fires_every_tick(self):
        register(name="s", cadence=every_tick(), scope=global_scope(), run=self._recording_run)
        for t in (0.0, 1.0, 2.0):
            self.clock.set(t)
            self.driver.tick()
        self.assertEqual(len(self.fires), 3)

    def test_dt_nominal_on_first_fire_then_real(self):
        register(name="s", cadence=every_tick(), scope=global_scope(), run=self._recording_run)
        self.clock.set(0.0)
        self.driver.tick()
        self.assertEqual(self.fires[0].dt, systems.TICK_INTERVAL)
        self.clock.set(3.0)  # late tick
        self.driver.tick()
        self.assertEqual(self.fires[1].dt, 3.0)


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


class TestErrorIsolation(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_failing_system_is_isolated_and_logged(self):
        def _boom(ctx):
            raise RuntimeError("kaboom")

        register(name="bad", cadence=every_tick(), scope=global_scope(), run=_boom)
        register(name="good", cadence=every_tick(), scope=global_scope(), run=self._recording_run)

        with patch.object(systems, "logger") as mock_logger:
            self.driver.tick()
            self.clock.advance(1.0)
            self.driver.tick()

        # the peer fired both ticks despite the failing sibling
        self.assertEqual(len(self.fires), 2)
        logged = " ".join(str(c) for c in mock_logger.log_err.call_args_list)
        self.assertIn("bad", logged)
        self.assertIn("kaboom", logged)


class TestOverlapGuard(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_due_while_in_flight_skips_and_warns(self):
        pending = Deferred()
        runs = []

        def _slow(ctx):
            runs.append(ctx)
            return pending

        register(name="slow", cadence=every_tick(), scope=global_scope(), run=_slow)
        self.driver.tick()
        self.assertEqual(len(runs), 1)

        with patch.object(systems, "logger") as mock_logger:
            self.clock.advance(1.0)
            self.driver.tick()  # still in flight -> skip
        self.assertEqual(len(runs), 1)
        self.assertTrue(mock_logger.log_warn.called)

        pending.callback(None)  # body completes
        self.clock.advance(1.0)
        self.driver.tick()
        self.assertEqual(len(runs), 2)

    def test_async_failure_clears_in_flight_and_logs(self):
        pending = Deferred()
        register(name="slow", cadence=every_tick(), scope=global_scope(), run=lambda ctx: pending)
        self.driver.tick()
        with patch.object(systems, "logger") as mock_logger:
            pending.errback(RuntimeError("async kaboom"))
        self.assertTrue(mock_logger.log_err.called)
        self.assertFalse(get_system("slow").in_flight)


class TestRegistry(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_duplicate_name_raises(self):
        register(name="s", cadence=every(5), scope=global_scope(), run=self._recording_run)
        with self.assertRaises(SystemRegistrationError):
            register(name="s", cadence=every(9), scope=global_scope(), run=self._recording_run)

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
        register(name="g", cadence=every_tick(), scope=global_scope(), run=self._recording_run)
        self.driver.tick()
        ctx = self.fires[0]
        self.assertIsNone(ctx.entities)
        self.assertIsNone(ctx.entity_ids)

    def test_online_puppets_uses_session_handler(self):
        puppets = ["puppet1", "puppet2"]
        register(name="p", cadence=every_tick(), scope=online_puppets(), run=self._recording_run)
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
                name="from-module", cadence=every(5), scope=global_scope(), run=lambda ctx: None
            )

        self._fake_module("fake_systems_good", register_fn=_register)
        systems.load_system_modules()
        self.assertIsNotNone(get_system("from-module"))

    @override_settings(SYSTEM_MODULES=[])
    def test_engine_systems_always_load(self):
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
        with patch.object(
            engine_systems, "_flush_all_dirty", return_value={"backends": 1, "total": 2}
        ) as flush:
            self.clock.set(0.0)
            self.driver.tick()  # prime
            self.clock.set(30.0)
            self.driver.tick()  # fire
        flush.assert_called_once()

    @override_settings(ATTRIBUTE_FLUSH_INTERVAL=30)
    def test_flush_failure_is_isolated_and_escalates(self):
        engine_systems = self._register_flush()
        system = get_system("flush-attributes")
        ctx = systems.SystemContext(now=0.0, dt=30.0)
        with patch.object(engine_systems, "_flush_all_dirty", side_effect=RuntimeError("pg down")):
            with patch.object(engine_systems, "logger") as mock_logger:
                for _ in range(3):
                    system.run(ctx)  # must not raise
        logged = " ".join(str(c) for c in mock_logger.log_err.call_args_list)
        self.assertIn("CRITICAL", logged)


class TestDriverLifecycle(_SchedulerTestMixin, BaseEvenniaTestCase):
    def test_start_runs_looping_call_at_tick_interval(self):
        with patch.object(self.driver._loop, "start") as mock_start:
            self.driver.start()
        mock_start.assert_called_once_with(systems.TICK_INTERVAL, now=False)

    def test_stop_is_safe_when_not_running(self):
        self.driver.stop()  # no raise

    def test_empty_registry_tick_is_a_noop(self):
        self.driver.tick()  # no raise, nothing registered
