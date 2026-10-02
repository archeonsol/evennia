"""
Tests for evennia.server.db_pool: the process-wide pool of raw database connections.

The pool is exercised with stand-in connections and a manual clock, so every rule
(age, capacity, health, ordering, fork safety) is checked without a database. The
backend that parks real connections in it is covered in test_db_backend_pool.
"""

from django.test import SimpleTestCase

from evennia.server import db_pool


class _Raw:
    """Stand-in for a DB-API connection."""

    def __init__(self, name, *, idle=True, hung_up=False):
        self.name = name
        self.closed = 0
        self.idle = idle
        self.hung_up = hung_up
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.closed = 1

    def __repr__(self):
        return f"<Raw {self.name}>"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _PoolCase(SimpleTestCase):
    KEY = ("evennia", "127.0.0.1", 6432, "evennia")

    def setUp(self):
        super().setUp()
        self.clock = _Clock()
        self.events = []
        self.pid = 4242
        self.pool = db_pool.ConnectionPool(
            max_idle=2,
            max_age=600.0,
            clock=self.clock,
            is_idle=lambda raw: raw.idle,
            hung_up=lambda raw: raw.hung_up,
            pid=lambda: self.pid,
            on_event=self.events.append,
        )

    def park(self, name="a", *, born=None, **kwargs):
        raw = _Raw(name, **kwargs)
        accepted = self.pool.checkin(self.KEY, raw, self.clock() if born is None else born)
        return raw, accepted


class TestCheckout(_PoolCase):
    def test_an_empty_pool_has_nothing_to_offer(self):
        self.assertIsNone(self.pool.checkout(self.KEY))

    def test_a_parked_connection_comes_back(self):
        raw, _ = self.park()

        entry = self.pool.checkout(self.KEY)

        self.assertIs(entry.connection, raw)
        self.assertEqual(self.events, ["parked", "reused"])
        self.assertEqual(self.pool.idle(), 0)

    def test_the_most_recently_parked_connection_is_reused_first(self):
        older, _ = self.park("older")
        newer, _ = self.park("newer")

        self.assertIs(self.pool.checkout(self.KEY).connection, newer)
        self.assertIs(self.pool.checkout(self.KEY).connection, older)

    def test_connections_to_another_database_are_not_offered(self):
        self.park()

        self.assertIsNone(self.pool.checkout(("other", "127.0.0.1", 6432, "evennia")))
        self.assertEqual(self.pool.idle(), 1)

    def test_a_connection_the_server_hung_up_on_is_discarded_and_the_next_one_tried(self):
        good, _ = self.park("good")
        dead, _ = self.park("dead")
        dead.hung_up = True

        entry = self.pool.checkout(self.KEY)

        self.assertIs(entry.connection, good)
        self.assertEqual(dead.close_calls, 1)
        self.assertIn("discarded", self.events)

    def test_a_connection_past_its_age_is_retired(self):
        raw, _ = self.park()
        self.clock.advance(601.0)

        self.assertIsNone(self.pool.checkout(self.KEY))
        self.assertEqual(raw.close_calls, 1)

    def test_the_age_counts_from_the_physical_connect_not_from_each_parking(self):
        raw, _ = self.park(born=self.clock() - 500.0)
        self.clock.advance(150.0)

        self.assertIsNone(self.pool.checkout(self.KEY))

    def test_a_connection_closed_while_parked_is_discarded(self):
        raw, _ = self.park()
        raw.closed = 1

        self.assertIsNone(self.pool.checkout(self.KEY))
        self.assertEqual(raw.close_calls, 0, "an already closed connection needs no close")


class TestCheckin(_PoolCase):
    def test_a_connection_in_a_transaction_is_refused(self):
        raw, accepted = self.park(idle=False)

        self.assertFalse(accepted)
        self.assertEqual(self.pool.idle(), 0)

    def test_a_closed_connection_is_refused(self):
        raw = _Raw("closed")
        raw.closed = 2

        self.assertFalse(self.pool.checkin(self.KEY, raw, self.clock()))

    def test_a_connection_past_its_age_is_refused(self):
        raw, accepted = self.park(born=self.clock() - 601.0)

        self.assertFalse(accepted)

    def test_the_oldest_idle_connection_makes_room_when_the_pool_is_full(self):
        oldest, _ = self.park("oldest")
        middle, _ = self.park("middle")
        newest, accepted = self.park("newest")

        self.assertTrue(accepted)
        self.assertEqual(self.pool.idle(), 2)
        self.assertEqual(oldest.close_calls, 1)
        self.assertEqual(middle.close_calls, 0)
        self.assertIn("evicted", self.events)

    def test_refusal_leaves_the_close_to_the_caller(self):
        raw, accepted = self.park(idle=False)

        self.assertFalse(accepted)
        self.assertEqual(raw.close_calls, 0)


class TestOpened(_PoolCase):
    def test_a_new_connection_reports_its_birth_time_and_the_event(self):
        born = self.pool.opened()

        self.assertEqual(born, self.clock())
        self.assertEqual(self.events, ["opened"])


class TestLifecycle(_PoolCase):
    def test_close_all_closes_every_idle_connection(self):
        first, _ = self.park("first")
        second, _ = self.park("second")

        closed = self.pool.close_all()

        self.assertEqual(closed, 2)
        self.assertEqual((first.close_calls, second.close_calls), (1, 1))
        self.assertEqual(self.pool.idle(), 0)

    def test_a_forked_child_drops_the_connections_it_inherited_without_closing_them(self):
        raw, _ = self.park()
        self.pid = 9999  # the process forked: these sockets belong to the parent

        self.assertIsNone(self.pool.checkout(self.KEY))
        self.assertEqual(raw.close_calls, 0)
        self.assertEqual(self.pool.idle(), 0)

    def test_a_failing_close_never_escapes(self):
        raw, _ = self.park()
        raw.close = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

        self.pool.close_all()  # must not raise


class TestConnectionKey(SimpleTestCase):
    def test_the_key_names_the_server_and_database_but_never_holds_the_password(self):
        params = {
            "dbname": "evennia",
            "user": "evennia",
            "password": "hunter2",
            "host": "127.0.0.1",
            "port": 6432,
            "client_encoding": "UTF8",
        }

        key = db_pool.connection_key(params)

        self.assertNotIn("hunter2", repr(key))
        self.assertIn("evennia", repr(key))
        self.assertEqual(key, db_pool.connection_key(dict(params)))

    def test_a_different_password_is_a_different_key(self):
        base = {"dbname": "evennia", "user": "evennia", "host": "h", "port": 1}

        self.assertNotEqual(
            db_pool.connection_key({**base, "password": "a"}),
            db_pool.connection_key({**base, "password": "b"}),
        )

    def test_a_different_database_is_a_different_key(self):
        base = {"user": "evennia", "host": "h", "port": 1, "password": "x"}

        self.assertNotEqual(
            db_pool.connection_key({**base, "dbname": "evennia"}),
            db_pool.connection_key({**base, "dbname": "test_evennia"}),
        )
