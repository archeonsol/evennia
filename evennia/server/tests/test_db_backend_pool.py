"""
Integration tests for the pooled PostgreSQL backend, against a real database.

A second wrapper is built on the test database with the engine's backend, so these
runs prove what the unit tests cannot: that a parked connection really is the same
server session when it comes back, that a connection the server killed is quietly
replaced, and that nothing unclean is ever parked. They need PostgreSQL and skip on
any other database.
"""

import copy
import time
import unittest

from django.db import connection
from django.test import override_settings

from evennia.server import db_pool
from evennia.utils.test_resources import EvenniaTestCase

ALIAS = "pooled_test"


def _wrapper():
    """A pooled-backend wrapper on the test database, outside Django's registry.

    A wrapper whose alias is not registered is a "dynamically created connection",
    which Django's test machinery lets through without declaring it.
    """
    from evennia.server.db_backend.postgresql.base import DatabaseWrapper

    return DatabaseWrapper(copy.deepcopy(connection.settings_dict), alias=ALIAS)


@unittest.skipUnless(connection.vendor == "postgresql", "the pool backend is PostgreSQL only")
@override_settings(ENGINE_DATABASE_POOL=True)
class TestPooledBackend(EvenniaTestCase):
    def setUp(self):
        super().setUp()
        db_pool.reset_pool()
        self.addCleanup(db_pool.reset_pool)
        self.conn = _wrapper()
        self.addCleanup(self.conn.close)

    def backend_pid(self, conn=None):
        with (conn or self.conn).cursor() as cursor:
            cursor.execute("select pg_backend_pid()")
            return cursor.fetchone()[0]

    def test_a_closed_connection_comes_back_as_the_same_server_session(self):
        first = self.backend_pid()
        self.conn.close()
        self.assertEqual(db_pool.get_pool().idle(), 1)

        second = self.backend_pid()

        self.assertEqual(first, second)
        self.assertTrue(self.conn.evennia_pool_reused)
        self.assertEqual(db_pool.get_pool().idle(), 0)

    def test_the_first_connection_is_opened_not_reused(self):
        self.backend_pid()

        self.assertFalse(self.conn.evennia_pool_reused)

    def test_a_second_wrapper_takes_over_a_parked_connection(self):
        first = self.backend_pid()
        self.conn.close()
        other = _wrapper()
        self.addCleanup(other.close)

        second = self.backend_pid(other)

        self.assertEqual(first, second)
        self.assertTrue(other.evennia_pool_reused)

    def test_two_wrappers_never_share_a_live_connection(self):
        other = _wrapper()
        self.addCleanup(other.close)

        first = self.backend_pid()
        second = self.backend_pid(other)

        self.assertNotEqual(first, second)

    def test_the_pool_off_means_every_connection_is_new(self):
        with override_settings(ENGINE_DATABASE_POOL=False):
            first = self.backend_pid()
            self.conn.close()
            second = self.backend_pid()

        self.assertNotEqual(first, second)
        self.assertEqual(db_pool.get_pool().idle(), 0)

    def test_a_connection_the_server_killed_while_parked_is_replaced_without_error(self):
        victim = self.backend_pid()
        self.conn.close()
        self.assertEqual(db_pool.get_pool().idle(), 1)
        with connection.cursor() as admin:
            admin.execute("select pg_terminate_backend(%s)", [victim])
        self._wait_until_the_socket_shows_it()

        replacement = self.backend_pid()

        self.assertNotEqual(victim, replacement)
        self.assertFalse(self.conn.evennia_pool_reused)

    def _wait_until_the_socket_shows_it(self):
        """The termination reaches the client socket a moment after pg_terminate_backend returns."""
        pool = db_pool.get_pool()
        raw = next(iter(pool._idle.values()))[0].connection
        for _ in range(100):
            if db_pool._default_hung_up(raw):
                return
            time.sleep(0.02)

    def test_a_connection_with_an_open_transaction_is_closed_not_parked(self):
        self.backend_pid()
        self.conn.connection.autocommit = False
        with self.conn.cursor() as cursor:
            cursor.execute("select 1")  # the driver opens a transaction

        self.conn.close()

        self.assertEqual(db_pool.get_pool().idle(), 0)

    def test_a_connection_after_a_database_error_is_not_parked(self):
        from django.db import DatabaseError

        self.backend_pid()
        with self.assertRaises(DatabaseError):
            with self.conn.cursor() as cursor:
                cursor.execute("select * from a_table_that_is_not_there")
        self.assertTrue(self.conn.errors_occurred)

        self.conn.close()

        self.assertEqual(db_pool.get_pool().idle(), 0)

    def test_a_connection_closed_inside_an_atomic_block_is_not_parked(self):
        self.backend_pid()
        self.conn.in_atomic_block = True

        self.conn.close()

        self.assertEqual(db_pool.get_pool().idle(), 0)

    def test_a_rolled_back_transaction_leaves_a_clean_connection_for_the_next_user(self):
        self.conn.set_autocommit(False)
        with self.conn.cursor() as cursor:
            cursor.execute("select 1")
        self.conn.rollback()
        self.conn.set_autocommit(True)
        self.conn.close()
        self.assertEqual(db_pool.get_pool().idle(), 1)

        with self.conn.cursor() as cursor:
            cursor.execute("select count(*) from accounts_accountdb where id > %s", [0])
            self.assertGreaterEqual(cursor.fetchone()[0], 0)

        self.assertTrue(self.conn.evennia_pool_reused)
        self.assertTrue(self.conn.get_autocommit())
