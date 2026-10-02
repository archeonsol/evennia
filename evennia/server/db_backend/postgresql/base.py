"""
PostgreSQL backend that parks closed connections in a pool and reuses them.

It is Django's own PostgreSQL backend with one change: when a wrapper "closes", the
underlying connection goes to :mod:`evennia.server.db_pool` instead of being
destroyed, and when a wrapper needs a connection it takes a parked one before it
opens a new one. The wrapper itself is untouched. It still belongs to a single
task, still holds its own transaction state, and is still discarded at the end of
the task, which is what keeps one task's transaction out of another's.

A connection is parked only if it is provably clean (see ``_can_park``), and handed
back out only if the server has not hung up on it. Turn it on with
``ENGINE_DATABASE_POOL = True``; ``apply_postgres_engine_defaults`` then points the
database at this backend. With psycopg 3, which has a pool of its own, the setting
does nothing.
"""

from django.conf import settings
from django.db.backends.postgresql import base as _postgresql

from evennia.server import db_pool


class DatabaseWrapper(_postgresql.DatabaseWrapper):
    """Django's PostgreSQL wrapper, recycling its physical connection through a pool.

    Attributes:
        evennia_pool_reused (bool): Whether this wrapper's current connection came
            back from the pool. The session-initialisation receiver reads it: a
            connection that has already been set up must not be set up again.
    """

    evennia_pool_reused = False
    _evennia_parked = None

    def _evennia_pool(self):
        """Return the pool to use, or ``None`` when connections are not recycled."""
        if not getattr(settings, db_pool.POOL_SETTING, False):
            return None
        if _postgresql.is_psycopg3 or self.pool:
            return None
        return db_pool.get_pool()

    def _set_isolation_level_attribute(self):
        """Record the isolation level Django would have, for a connection it did not open."""
        level = getattr(_postgresql, "IsolationLevel", None)
        if level is None:
            return
        value = self.settings_dict["OPTIONS"].get("isolation_level")
        try:
            self.isolation_level = level(value) if value is not None else level.READ_COMMITTED
        except ValueError:
            pass

    def get_new_connection(self, conn_params):
        """Take a parked connection if there is one, else open a new one."""
        pool = self._evennia_pool()
        self.evennia_pool_reused = False
        self._evennia_parked = None
        if pool is None:
            return super().get_new_connection(conn_params)
        key = db_pool.connection_key(conn_params)
        entry = pool.checkout(key)
        if entry is not None:
            self._set_isolation_level_attribute()
            self._evennia_parked = (pool, key, entry.born)
            self.evennia_pool_reused = True
            return entry.connection
        connection = super().get_new_connection(conn_params)
        self._evennia_parked = (pool, key, pool.opened())
        return connection

    def _can_park(self):
        """Whether this wrapper's connection is clean enough for the next task.

        Not in a transaction, no database error since it was opened, and the
        autocommit setting Django gave it. The pool re-checks the connection's own
        view (open, idle) before it accepts it.
        """
        connection = self.connection
        if connection is None or self.errors_occurred or self.in_atomic_block:
            return False
        try:
            return bool(connection.autocommit) == bool(self.settings_dict["AUTOCOMMIT"])
        except Exception:
            return False

    def _close(self):
        """Park the connection for reuse, or close it if it cannot be parked."""
        parked, self._evennia_parked = self._evennia_parked, None
        if parked is not None and self.connection is not None and self._can_park():
            pool, key, born = parked
            with self.wrap_database_errors:
                if pool.checkin(key, self.connection, born):
                    return None
        return super()._close()
