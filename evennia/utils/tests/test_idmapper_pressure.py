"""Tests for the idmapper's memory-pressure check: what it counts and when it sweeps."""

import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from evennia.utils.idmapper import models as idmapper


class TestCacheSize(SimpleTestCase):
    """The shared cache of one database class is counted once, not once per typeclass."""

    def test_typeclasses_of_one_database_class_share_one_count(self):
        class Root:
            pass

        class ObjectTable:
            __instance_cache__ = {1: object(), 2: object(), 3: object()}

        class AccountTable:
            __instance_cache__ = {10: object()}

        class Character(Root):
            __dbclass__ = ObjectTable

        class Room(Root):
            __dbclass__ = ObjectTable

        class Exit(Root):
            __dbclass__ = ObjectTable

        class Account(Root):
            __dbclass__ = AccountTable

        with patch.object(idmapper, "SharedMemoryModel", Root):
            total, by_class = idmapper.cache_size()

        self.assertEqual(total, 4)
        self.assertEqual(by_class, {"ObjectTable": 3, "AccountTable": 1})


class TestResidentMemory(SimpleTestCase):
    def test_reads_the_resident_pages_from_a_statm_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".statm", delete=False) as handle:
            handle.write("123456 51200 900 10 0 4000 0\n")

        self.assertAlmostEqual(idmapper._rss_from_statm(handle.name, page_size=4096), 200.0)

    def test_an_unreadable_statm_file_is_unknown_not_an_error(self):
        self.assertIsNone(idmapper._rss_from_statm("/no/such/statm", page_size=4096))

    def test_prefers_the_current_size_over_the_peak(self):
        fake = SimpleNamespace(
            Process=lambda pid: SimpleNamespace(
                memory_info=lambda: SimpleNamespace(rss=600 * 1024 * 1024)
            )
        )

        with patch.dict(sys.modules, {"psutil": fake}):
            self.assertAlmostEqual(idmapper._current_rss_mb(), 600.0)

    def test_falls_back_to_statm_when_psutil_is_absent(self):
        with (
            patch.dict(sys.modules, {"psutil": None}),
            patch.object(idmapper, "_rss_from_statm", return_value=321.0),
        ):
            self.assertEqual(idmapper._current_rss_mb(), 321.0)


class _PressureCase(SimpleTestCase):
    """Drive ``conditional_flush`` with a scripted clock, cache and process size."""

    LIMIT = 1000  # MB; the matching instance estimate is (1000 - 35) / 0.0157 = 61,464

    def setUp(self):
        super().setUp()
        self.now = 10_000.0
        self.rss = 100.0
        self.instances = 1000
        self.started = []
        patches = [
            patch.object(
                idmapper,
                "time",
                SimpleNamespace(time=lambda: self.now, monotonic=time.monotonic),
            ),
            patch.object(idmapper, "_current_rss_mb", lambda: self.rss),
            patch.object(idmapper, "cache_size", lambda: (self.instances, {})),
            patch.object(idmapper.clock, "loop_running", return_value=True),
            patch.object(
                idmapper,
                "_start_incremental_cache_flush",
                side_effect=lambda: self.started.append(self.now) or True,
            ),
            patch.object(idmapper.logger, "log_warn"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        idmapper.LAST_FLUSH = None
        idmapper.LAST_FLUSH_RSS = None
        self.addCleanup(setattr, idmapper, "LAST_FLUSH", None)
        self.addCleanup(setattr, idmapper, "LAST_FLUSH_RSS", None)
        idmapper.conditional_flush(self.LIMIT)  # the first call only starts the clock

    def check(self, advance=idmapper.AUTO_FLUSH_MIN_INTERVAL):
        self.now += advance
        idmapper.conditional_flush(self.LIMIT)


class TestPressureCheck(_PressureCase):
    def test_a_small_cache_in_a_small_process_is_left_alone(self):
        self.check()

        self.assertEqual(self.started, [])

    def test_resident_memory_over_the_limit_sweeps_whatever_the_cache_count(self):
        self.rss = 1200.0
        self.instances = 3000  # nowhere near the instance estimate

        self.check()

        self.assertEqual(len(self.started), 1)

    def test_a_full_cache_sweeps_once_memory_is_close_to_the_limit(self):
        self.instances = 70_000
        self.rss = 950.0

        self.check()

        self.assertEqual(len(self.started), 1)

    def test_a_full_cache_in_a_small_process_is_left_alone(self):
        self.instances = 70_000
        self.rss = 400.0

        self.check()

        self.assertEqual(self.started, [])

    def test_a_large_process_with_a_small_cache_is_not_swept_below_the_limit(self):
        self.rss = 990.0

        self.check()

        self.assertEqual(self.started, [])

    def test_checks_inside_the_minimum_interval_are_ignored(self):
        self.rss = 1200.0

        self.check(advance=30.0)

        self.assertEqual(self.started, [])

    def test_unknown_resident_memory_leaves_the_decision_to_the_count(self):
        with patch.object(idmapper, "_current_rss_mb", lambda: None):
            self.instances = 3000
            self.check()
            self.assertEqual(self.started, [])

            self.instances = 70_000
            self.check()
            self.assertEqual(len(self.started), 1)

    def test_a_disabled_limit_never_sweeps(self):
        self.rss = 10**6
        self.now += idmapper.AUTO_FLUSH_MIN_INTERVAL

        idmapper.conditional_flush(None)

        self.assertEqual(self.started, [])


class TestSweepHysteresis(_PressureCase):
    """A process that stays over its limit is not swept again and again."""

    def test_no_second_sweep_until_memory_has_grown_ten_percent(self):
        self.rss = 1200.0
        self.check()
        self.assertEqual(len(self.started), 1)

        self.rss = 1250.0  # the sweep did not bring it down, and it has barely grown
        self.check()
        self.assertEqual(len(self.started), 1)

        self.rss = 1320.0  # 10% past where the last sweep started
        self.check()
        self.assertEqual(len(self.started), 2)

    def test_a_forced_flush_ignores_the_hysteresis(self):
        self.rss = 1200.0
        self.check()
        self.now += 1.0

        idmapper.conditional_flush(self.LIMIT, force=True)

        self.assertEqual(len(self.started), 2)

    def test_a_flush_that_could_not_start_does_not_arm_the_hysteresis(self):
        self.rss = 1200.0
        with patch.object(idmapper, "_start_incremental_cache_flush", return_value=False):
            self.check()
        self.assertIsNone(idmapper.LAST_FLUSH_RSS)

        self.check()

        self.assertEqual(len(self.started), 1)
