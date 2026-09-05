"""Tests for the suite runner's identity-cache flush.

The defect these lock in is not a crash in one test. It is one test leaving a
rolled-back row in the identity cache and a *different* test, in a different
module, failing at ``COMMIT`` with a traceback that names neither of them. The
order the modules run in decides whether it appears at all, which is why it
survived until two suites were named on one command line.

"""

import unittest

from django.test import TestCase

from evennia.objects.models import ObjectDB
from evennia.server.tests.testrunner import (
    EvenniaTestSuiteRunner,
    IdmapperFlushResultMixin,
    flush_identity_caches,
)


class TestFlushIdentityCaches(TestCase):
    """The flush itself."""

    def test_it_empties_a_populated_cache(self):
        obj = ObjectDB.objects.create(db_key="cached")
        self.assertIsNotNone(ObjectDB.get_cached_instance(obj.pk))
        flush_identity_caches()
        self.assertIsNone(ObjectDB.get_cached_instance(obj.pk))

    def test_a_dbref_no_longer_resolves_to_a_deleted_row(self):
        # The exact shape of the production failure: the row is gone, the
        # instance is not, and a lookup by primary key answers from memory. A
        # foreign key written to that answer fails at COMMIT.
        from evennia.utils.utils import dbid_to_obj

        obj = ObjectDB.objects.create(db_key="ghost")
        pk = obj.pk
        ObjectDB.objects.filter(pk=pk).delete()
        ObjectDB.__dbclass__.__instance_cache__[pk] = obj
        self.assertIs(dbid_to_obj(f"#{pk}", ObjectDB), obj)

        # Once the cache is empty the database is consulted and reports the
        # truth. create_object depends on exactly this: it catches DoesNotExist
        # and leaves the home unset, instead of writing a foreign key to a row
        # that is not there.
        flush_identity_caches()
        with self.assertRaises(ObjectDB.DoesNotExist):
            dbid_to_obj(f"#{pk}", ObjectDB)

    def test_it_empties_the_jsonb_row_states(self):
        # The second cache with the same problem. A rolled-back primary key is
        # reissued, so without this the next row created at pk=1 inherits the
        # previous test's attributes and every key looks like it already
        # existed.
        from evennia.typeclasses import jsonb_handler

        obj = ObjectDB.objects.create(db_key="attributed")
        obj.attributes.add("hp", 10)
        self.assertTrue(obj.attributes.has("hp"))
        self.assertTrue(jsonb_handler._ROW_STATES or jsonb_handler._STRONG_ROW_STATES)

        flush_identity_caches()
        self.assertFalse(jsonb_handler._ROW_STATES)
        self.assertFalse(jsonb_handler._STRONG_ROW_STATES)

    def test_it_runs_with_the_caches_already_empty(self):
        flush_identity_caches()
        flush_identity_caches()


class TestResultClass(unittest.TestCase):
    """The runner has to compose with Django's own result classes."""

    def test_the_flush_is_installed(self):
        self.assertTrue(
            issubclass(EvenniaTestSuiteRunner().get_resultclass(), IdmapperFlushResultMixin)
        )

    def test_djangos_choice_is_kept(self):
        # --debug-sql and --pdb each select a result class of their own. The
        # flush is mixed into whichever one Django picked, not put in its place.
        composed = EvenniaTestSuiteRunner(debug_sql=True).get_resultclass()
        self.assertTrue(issubclass(composed, IdmapperFlushResultMixin))
        self.assertIn("DebugSQL", " ".join(cls.__name__ for cls in composed.__mro__))

    def test_the_flush_happens_before_the_test_is_reported(self):
        # Ordering is the whole point: a result class that reported first and
        # flushed afterwards would leave the window this closes wide open.
        seen = []

        class Recorder(unittest.TestResult):
            def stopTest(self, test):
                seen.append("empty" if not ObjectDB.__dbclass__.__instance_cache__ else "stale")
                super().stopTest(test)

        class Composed(IdmapperFlushResultMixin, Recorder):
            pass

        ObjectDB.__dbclass__.__instance_cache__ = {1: object()}
        Composed().stopTest(self)
        self.assertEqual(seen, ["empty"])


class TestParallelWorkerMode(unittest.TestCase):
    """Spawned workers must initialize the Server before importing fixtures."""

    def test_server_handler_is_available(self):
        """Expose the Server session API in each spawned worker."""
        import evennia

        self.assertTrue(callable(getattr(evennia.SESSION_HANDLER, "portal_connect", None)))

    def test_remote_results_flush_in_the_worker(self):
        """Install cache cleanup on the result class used inside workers."""
        runner = EvenniaTestSuiteRunner.parallel_test_suite.runner_class()
        self.assertIsInstance(runner.resultclass(), IdmapperFlushResultMixin)

    def test_engine_initialization_follows_database_clone_assignment(self):
        """Keep Server database access after Django attaches the worker clone."""
        from unittest.mock import patch

        import evennia
        from evennia.server.tests import testrunner

        events = []
        with (
            patch.object(
                testrunner,
                "_django_init_worker",
                side_effect=lambda *args: events.append("clone"),
                create=True,
            ),
            patch.object(evennia, "_init", side_effect=lambda: events.append("engine")),
        ):
            testrunner.initialize_worker("counter")
        self.assertEqual(events, ["clone", "engine"])


class TestParallelWorkerRollback(TestCase):
    """Consecutive methods stay in one worker and expose stale row identities."""

    def test_a_cache_a_transactional_row(self):
        """Leave an actual cached row for the result cleanup to discard."""
        obj = ObjectDB.objects.create(id=900001, db_key="worker rollback")
        obj.attributes.add("worker-state", "must disappear")
        self.assertIs(ObjectDB.get_cached_instance(obj.pk), obj)

    def test_b_rolled_back_row_is_absent_from_memory(self):
        """Reject identities left by the preceding rolled-back transaction."""
        self.assertFalse(ObjectDB.objects.filter(pk=900001).exists())
        self.assertIsNone(ObjectDB.get_cached_instance(900001))
        from evennia.typeclasses import jsonb_handler

        self.assertFalse(jsonb_handler._ROW_STATES)
        self.assertFalse(jsonb_handler._STRONG_ROW_STATES)
