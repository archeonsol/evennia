"""
Main test-suite runner of Evennia. The runner collates tests from
all over the code base and runs them.

Runs as part of the Evennia's test suite with 'evennia test evennia"

"""

import unittest

from django.test.runner import DiscoverRunner


def flush_identity_caches():
    """Empty every idmapper identity cache.

    The identity cache holds one Python instance per row and answers a lookup
    by primary key from memory, without touching the database. That is correct
    only while the database is the source of truth for what exists.

    A ``TestCase`` rolls its transaction back, which deletes the rows but not
    the instances that map to them. From that point a lookup by dbref returns
    an object whose row is gone, and writing a foreign key to it fails at
    ``COMMIT`` -- in a later test, in a different module, with a traceback that
    names neither the test that created the row nor the rollback that removed
    it. The observed case was ``DEFAULT_HOME = "#2"`` resolving to a rolled-back
    fixture, so every following ``create_object`` raised ``FOREIGN KEY
    constraint failed``.

    The rollback is therefore paired with a flush, once per test, for every
    test.

    Notes:
        The public ``flush_instance_cache`` asserts IO ownership, which a test
        that rebound the clock and did not restore it no longer holds. Between
        tests there is no IO thread to protect, and a surviving cache is the
        exact defect this exists to prevent, so the caches are reset directly.

    """

    from evennia.utils.idmapper.models import _cancel_active_cache_flush, _leaf_cache_models

    _cancel_active_cache_flush()
    for model in _leaf_cache_models():
        model.__instance_cache__ = {}


class IdmapperFlushResultMixin:
    """Flush the identity caches after each test.

    ``stopTest`` runs after ``tearDown`` and after Django has rolled the test
    transaction back, which is the first moment the caches are known to be
    stale and the last moment before another test can read them.
    """

    def stopTest(self, test):
        """Flush the identity caches, then report the test as finished."""

        flush_identity_caches()
        super().stopTest(test)


class EvenniaTestSuiteRunner(DiscoverRunner):
    """
    Pointed to by the TEST_RUNNER setting.
    This test runner only runs tests on the apps specified in evennia/
     avoid running the large number of tests defined by Django

    """

    def get_resultclass(self):
        """Return Django's result class with the identity-cache flush added.

        Composed rather than replaced, so ``--debug-sql`` and ``--pdb`` keep
        the result classes they select.
        """

        base = super().get_resultclass() or unittest.TextTestResult
        return type(f"Idmapper{base.__name__}", (IdmapperFlushResultMixin, base), {})

    def setup_test_environment(self, **kwargs):
        import evennia

        evennia._init()

        from django.conf import settings

        # set testing flag while suite runs
        settings.TEST_ENVIRONMENT = True
        super().setup_test_environment(**kwargs)

    def teardown_test_environment(self, **kwargs):
        # remove testing flag after suite has run
        from django.conf import settings

        settings.TEST_ENVIRONMENT = False

        super().teardown_test_environment(**kwargs)
