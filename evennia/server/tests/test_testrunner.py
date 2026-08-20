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
