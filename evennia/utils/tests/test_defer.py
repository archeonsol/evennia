"""
Tests for evennia.utils.defer (AS1 threaded-I/O helpers).

The reactor is not *run* inside Django's test runner, so two pieces of plumbing
that production relies on are arranged by hand here:

- The reactor thread pool is normally started via ``callWhenRunning``; with no
  running reactor it would never start (and its non-daemon workers would, if
  left started, block interpreter exit). Each test installs a fresh, started
  ``ThreadPool`` as ``reactor.threadpool`` and stops it in ``tearDown``, so
  ``deferToThread`` has real workers and the process still exits cleanly.
- ``deferToThread`` delivers its callback through ``reactor.callFromThread``,
  which only queues the call; we drain that queue with
  ``reactor.runUntilCurrent`` on the main thread. The main (test) thread is the
  stand-in for the reactor thread, so a callback that runs during a
  ``runUntilCurrent`` drain has demonstrably run on the reactor thread, not the
  worker thread.
"""

import threading
import time
from unittest.mock import patch

from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.python.threadpool import ThreadPool

from evennia.utils import defer
from evennia.utils.test_resources import BaseEvenniaTestCase

_DRAIN_TIMEOUT = 5.0


class _RealPoolMixin:
    """Install a fresh, started reactor thread pool for the duration of a test."""

    def setUp(self):
        super().setUp()
        self._orig_pool = reactor.threadpool
        self._pool = ThreadPool(minthreads=0, maxthreads=4, name="test_defer")
        self._pool.start()
        reactor.threadpool = self._pool

    def tearDown(self):
        reactor.threadpool = self._orig_pool
        self._pool.stop()
        super().tearDown()

    def _drain_until(self, done):
        """Pump the reactor's thread-call queue on this thread until `done`."""
        deadline = time.time() + _DRAIN_TIMEOUT
        while not done.is_set():
            reactor.runUntilCurrent()
            if done.wait(0.01):
                break
            if time.time() > deadline:
                self.fail("timed out waiting for reactor callback delivery")


class TestInThread(_RealPoolMixin, BaseEvenniaTestCase):
    """`in_thread` runs the worker off the reactor thread, callback on it."""

    def test_worker_runs_off_reactor_thread_callback_runs_on_it(self):
        main_ident = threading.get_ident()
        box = {}
        done = threading.Event()

        def worker(payload):
            box["worker_ident"] = threading.get_ident()
            return payload * 2

        def on_result(result):
            box["callback_ident"] = threading.get_ident()
            box["result"] = result
            done.set()
            return result

        d = defer.in_thread(worker, 21)
        self.assertIsInstance(d, Deferred)
        d.addCallback(on_result)

        self._drain_until(done)

        self.assertNotEqual(box["worker_ident"], main_ident)  # worker off reactor thread
        self.assertEqual(box["callback_ident"], main_ident)  # callback on reactor thread
        self.assertEqual(box["result"], 42)

    def test_kwargs_are_forwarded_to_worker(self):
        done = threading.Event()
        box = {}

        def worker(a, b=0):
            return a + b

        defer.in_thread(worker, 5, b=7).addCallback(
            lambda r: (box.__setitem__("result", r), done.set())
        )
        self._drain_until(done)
        self.assertEqual(box["result"], 12)


class TestThreaded(_RealPoolMixin, BaseEvenniaTestCase):
    """`threaded` decorator turns a call into an `in_thread` Deferred."""

    def test_decorated_call_returns_deferred_and_runs_in_thread(self):
        main_ident = threading.get_ident()
        box = {}
        done = threading.Event()

        @defer.threaded
        def fetch(x):
            box["worker_ident"] = threading.get_ident()
            return x

        d = fetch("ok")
        self.assertIsInstance(d, Deferred)
        d.addCallback(lambda r: (box.__setitem__("result", r), done.set()))

        self._drain_until(done)

        self.assertNotEqual(box["worker_ident"], main_ident)
        self.assertEqual(box["result"], "ok")


class TestBackground(BaseEvenniaTestCase):
    """
    `background` is fire-and-forget; its error routing is reactor-thread
    callback logic independent of real threading, so these patch `in_thread`
    with a synchronous Deferred we drive by hand. This isolates the routing
    contract: on_error gets called, the log fires by default, the failure
    never propagates to the caller, and the return is None.
    """

    def test_returns_none(self):
        with patch.object(defer, "in_thread", return_value=Deferred()):
            result = defer.background(lambda: None)
        self.assertIsNone(result)

    def test_exception_routed_to_on_error_not_logged(self):
        d = Deferred()
        seen = []

        with patch.object(defer, "in_thread", return_value=d):
            defer.background(lambda: None, on_error=seen.append)

        with patch.object(defer.logger, "log_err") as mock_err:
            try:
                raise ValueError("boom")
            except ValueError:
                d.errback()

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].type, ValueError)
        mock_err.assert_not_called()  # on_error given -> no default logging

    def test_exception_logged_by_default(self):
        d = Deferred()

        with patch.object(defer, "in_thread", return_value=d):
            defer.background(lambda: None)

        with patch.object(defer.logger, "log_err") as mock_err:
            try:
                raise ValueError("boom")
            except ValueError:
                d.errback()

        mock_err.assert_called_once()

    def test_failure_does_not_propagate(self):
        # A handled errback must leave the Deferred with no lingering failure,
        # so Twisted does not log an unhandled error at GC time.
        d = Deferred()
        with patch.object(defer, "in_thread", return_value=d):
            defer.background(lambda: None)

        with patch.object(defer.logger, "log_err"):
            try:
                raise ValueError("boom")
            except ValueError:
                d.errback()

        collected = []
        d.addBoth(collected.append)
        self.assertEqual(collected, [None])  # errback returned None -> chain clean
