"""
A process-wide pool of raw PostgreSQL connections for the engine's Django backend.

The game loop gives every callback, command and scheduled system its own Django
connection wrapper and closes it when the work ends, so a wrapper never outlives
one unit of work. That isolation is right (a transaction opened by one task must
never be visible to another), but it meant every unit of work that touched the
database paid for a fresh connection: a TCP connect, the pooler's login, two
``SET`` statements and a close. On production that was about five connections a
second, every one of them lasting under a second, and roughly 12% of everything
the reactor did.

The pool keeps the isolation and drops the cost. The wrapper still belongs to one
task and still "closes" when the task ends, but closing parks the underlying
connection here instead of destroying it, and the next wrapper that needs one
takes it back. A connection is only parked when it is demonstrably clean: open,
idle (no transaction), and not left in an odd autocommit state by its last user.
It is only handed out again if the server has not hung up on it in the meantime,
which is a poll of the socket, not a round trip.

A PgBouncer in transaction mode multiplexes server connections per transaction, so
holding the *client* side open costs the database nothing.
"""

from __future__ import annotations

import os
import select
import threading
import time
from collections import deque
from dataclasses import dataclass

#: Name of the setting that turns the pool on (read by the backend, not here).
POOL_SETTING = "ENGINE_DATABASE_POOL"


@dataclass(frozen=True)
class PoolEntry:
    """One idle connection.

    Attributes:
        connection: The raw DB-API connection.
        born (float): When it was physically opened, on the pool's clock. Its age
            is counted from here, not from the latest parking, so a connection
            that is reused all day still retires on schedule.
        parked (float): When it was last returned.
    """

    connection: object
    born: float
    parked: float


def connection_key(params):
    """Fingerprint the connection parameters that decide whether two connections are alike.

    The password takes part, so a changed credential never reuses an old session,
    but it is reduced to a per-process hash: the key is logged and shown in
    tracebacks, and must not carry it.

    Args:
        params (dict): The parameters Django passes to the driver.

    Returns:
        tuple: A hashable key.
    """
    shown = tuple(sorted((str(k), repr(v)) for k, v in params.items() if k != "password"))
    return shown, hash(params.get("password"))


def _default_is_idle(raw):
    """Whether a psycopg2 connection is open outside any transaction."""
    try:
        return raw.info.transaction_status == 0  # psycopg2 TRANSACTION_STATUS_IDLE
    except Exception:
        return False


def _readable_now(fd):
    """Whether a socket has anything to read this instant, without waiting."""
    if hasattr(select, "poll"):
        poller = select.poll()
        poller.register(fd, select.POLLIN | select.POLLPRI | select.POLLERR | select.POLLHUP)
        return bool(poller.poll(0))
    return bool(select.select([fd], [], [], 0)[0])


def _default_hung_up(raw):
    """Whether the server closed this connection while it sat idle.

    A parked connection has no query in flight, so anything readable on its socket
    is the server saying goodbye (a terminated backend, a restarted pooler) or a
    stray message. Either way it is not safe to reuse. This is one poll call, with
    no round trip.
    """
    try:
        return _readable_now(raw.fileno())
    except Exception:
        return True


class ConnectionPool:
    """Idle raw connections, kept per distinct connection key.

    Args:
        max_idle (int, optional): Idle connections kept per key. A returned
            connection past this evicts the oldest idle one.
        max_age (float, optional): Seconds a connection may live from the moment it
            was opened, however often it is reused.
        clock (callable, optional): Monotonic clock, injectable for testing.
        is_idle (callable, optional): ``is_idle(raw) -> bool``, whether a connection
            is outside a transaction. Injectable for testing.
        hung_up (callable, optional): ``hung_up(raw) -> bool``, whether the server
            closed it. Injectable for testing.
        pid (callable, optional): Returns the process id, injectable for testing.
        on_event (callable, optional): Called with ``reused``, ``parked``,
            ``evicted`` or ``discarded`` as each happens, for metrics.
    """

    def __init__(
        self,
        *,
        max_idle=16,
        max_age=1800.0,
        clock=time.monotonic,
        is_idle=_default_is_idle,
        hung_up=_default_hung_up,
        pid=os.getpid,
        on_event=None,
    ):
        self._max_idle = max(1, int(max_idle))
        self._max_age = float(max_age)
        self._clock = clock
        self._is_idle = is_idle
        self._hung_up = hung_up
        self._pid_of = pid
        self._pid = pid()
        self._on_event = on_event
        self._idle = {}
        self._lock = threading.Lock()

    def _emit(self, event):
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                pass

    @staticmethod
    def _close_quietly(raw):
        """Close a connection the pool is done with; a failure here is not news."""
        try:
            raw.close()
        except Exception:
            pass

    def _forget_inherited(self):
        """After a fork the idle sockets belong to the parent: drop them, never close them."""
        if self._pid_of() != self._pid:
            self._idle = {}
            self._pid = self._pid_of()

    def opened(self):
        """Note that the caller just opened a connection.

        Returns:
            float: Its birth time on this pool's clock, to hand back to
            :meth:`checkin` so its age is counted from the physical connect.
        """
        self._emit("opened")
        return self._clock()

    def idle(self):
        """Return how many connections are parked right now."""
        with self._lock:
            return sum(len(bucket) for bucket in self._idle.values())

    def checkout(self, key):
        """Take back the most recently parked usable connection for ``key``.

        Args:
            key (tuple): From :func:`connection_key`.

        Returns:
            PoolEntry or None: A clean connection, or ``None`` when the caller has
            to open one.
        """
        discarded = []
        entry = None
        with self._lock:
            self._forget_inherited()
            bucket = self._idle.get(key)
            now = self._clock()
            while bucket:
                candidate = bucket.pop()  # newest first: it is the likeliest to be alive
                raw = candidate.connection
                if raw.closed:
                    continue
                if now - candidate.born >= self._max_age or self._hung_up(raw):
                    discarded.append(raw)
                    continue
                entry = candidate
                break
            if bucket is not None and not bucket:
                self._idle.pop(key, None)
        for raw in discarded:
            self._close_quietly(raw)
            self._emit("discarded")
        if entry is not None:
            self._emit("reused")
        return entry

    def checkin(self, key, raw, born):
        """Park a connection for reuse, if it is clean.

        Args:
            key (tuple): From :func:`connection_key`.
            raw: The raw DB-API connection.
            born (float): When it was opened, on this pool's clock.

        Returns:
            bool: Whether the pool took it. When ``False`` the caller still owns the
            connection and must close it.
        """
        now = self._clock()
        if raw.closed or now - born >= self._max_age or not self._is_idle(raw):
            return False
        evicted = None
        with self._lock:
            self._forget_inherited()
            bucket = self._idle.setdefault(key, deque())
            bucket.append(PoolEntry(raw, born, now))
            if len(bucket) > self._max_idle:
                evicted = bucket.popleft().connection
        if evicted is not None:
            self._close_quietly(evicted)
            self._emit("evicted")
        self._emit("parked")
        return True

    def close_all(self):
        """Close every parked connection (shutdown, or a test that must release a database).

        Returns:
            int: How many connections were closed.
        """
        with self._lock:
            buckets, self._idle = self._idle, {}
        closed = 0
        for bucket in buckets.values():
            for entry in bucket:
                self._close_quietly(entry.connection)
                closed += 1
        return closed


_POOL = None
_POOL_LOCK = threading.Lock()


def _record_event(event):
    """Forward a pool event to Prometheus, never letting telemetry break a connection."""
    from evennia.server import prometheus_metrics

    prometheus_metrics.best_effort("db pool", prometheus_metrics.record_db_pool_event, event)


def get_pool():
    """Return the process's pool, building it from settings on first use."""
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                from django.conf import settings

                pool = ConnectionPool(
                    max_idle=getattr(settings, "ENGINE_DATABASE_POOL_MAX_IDLE", 16),
                    max_age=getattr(settings, "ENGINE_DATABASE_POOL_MAX_AGE", 1800),
                    on_event=_record_event,
                )
                try:
                    from evennia.utils import clock

                    clock.register_shutdown_hook(pool.close_all)
                except Exception:
                    pass
                _POOL = pool
    return _POOL


def reset_pool():
    """Close and forget the process's pool. Test isolation, or after a settings change."""
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.close_all()
