"""
Tests for evennia.utils.defer (AS1 threaded-I/O helpers).

Uses a dedicated asyncio event loop per test. ``in_thread`` returns an
``asyncio.Future`` from ``clock.defer_to_thread``; results are collected via
``await`` inside ``loop.run_until_complete``.
"""

import asyncio
import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.utils import defer

_DRAIN_TIMEOUT = 5.0


class _AsyncioLoopMixin:
    """Install a fresh asyncio event loop for the duration of a test."""

    def setUp(self):
        super().setUp()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()
        asyncio.set_event_loop(None)
        super().tearDown()


class TestInThread(_AsyncioLoopMixin, SimpleTestCase):
    """`in_thread` runs the worker off the loop thread; await resumes on it."""

    def test_worker_runs_off_loop_thread_callback_runs_on_it(self):
        main_ident = threading.get_ident()
        box = {}

        def worker(payload):
            box["worker_ident"] = threading.get_ident()
            return payload * 2

        async def _run():
            fut = defer.in_thread(worker, 21)
            self.assertIsInstance(fut, asyncio.Future)
            result = await fut
            box["callback_ident"] = threading.get_ident()
            box["result"] = result

        self._loop.run_until_complete(_run())

        self.assertNotEqual(box["worker_ident"], main_ident)
        self.assertEqual(box["callback_ident"], main_ident)
        self.assertEqual(box["result"], 42)

    def test_kwargs_are_forwarded_to_worker(self):
        box = {}

        def worker(a, b=0):
            return a + b

        async def _run():
            box["result"] = await defer.in_thread(worker, 5, b=7)

        self._loop.run_until_complete(_run())
        self.assertEqual(box["result"], 12)

    def test_db_connections_cleaned_around_worker_on_worker_thread(self):
        main_ident = threading.get_ident()
        calls = []

        def worker():
            return "x"

        with patch.object(
            defer, "close_old_connections", lambda: calls.append(threading.get_ident())
        ):

            async def _run():
                await defer.in_thread(worker)

            self._loop.run_until_complete(_run())

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(ident != main_ident for ident in calls))

    def test_add_callbacks_runs_on_loop_thread(self):
        main_ident = threading.get_ident()
        box = {}

        def worker():
            return [1, 2, 3]

        def _apply(result):
            box["callback_ident"] = threading.get_ident()
            box["result"] = result

        def _failed(failure):
            box["failed"] = failure.getTraceback()

        defer.in_thread(worker).addCallbacks(_apply, _failed)

        deadline = time.time() + _DRAIN_TIMEOUT
        while "result" not in box and time.time() < deadline:
            self._loop.run_until_complete(asyncio.sleep(0.05))

        self.assertEqual(box["callback_ident"], main_ident)
        self.assertEqual(box["result"], [1, 2, 3])
        self.assertNotIn("failed", box)

    def test_add_callbacks_errback_gets_worker_failure(self):
        box = {}

        def worker():
            raise ValueError("boom")

        defer.in_thread(worker).addCallbacks(
            lambda r: box.__setitem__("ok", r),
            lambda f: box.__setitem__("err", f.getTraceback()),
        )

        deadline = time.time() + _DRAIN_TIMEOUT
        while "err" not in box and time.time() < deadline:
            self._loop.run_until_complete(asyncio.sleep(0.05))

        self.assertIn("ValueError: boom", box["err"])
        self.assertNotIn("ok", box)


class TestThreaded(_AsyncioLoopMixin, SimpleTestCase):
    """`threaded` decorator turns a call into an `in_thread` Future."""

    def test_decorated_call_returns_future_and_runs_in_thread(self):
        main_ident = threading.get_ident()
        box = {}

        @defer.threaded
        def fetch(x):
            box["worker_ident"] = threading.get_ident()
            return x

        async def _run():
            fut = fetch("ok")
            self.assertIsInstance(fut, asyncio.Future)
            box["result"] = await fut

        self._loop.run_until_complete(_run())

        self.assertNotEqual(box["worker_ident"], main_ident)
        self.assertEqual(box["result"], "ok")


class TestBackground(_AsyncioLoopMixin, SimpleTestCase):
    """
    `background` is fire-and-forget; error routing runs on the event loop
    thread after the worker raises.
    """

    def test_returns_none(self):
        with patch.object(defer, "in_thread") as mock_in_thread:
            mock_in_thread.return_value = self._loop.create_future()
            result = defer.background(lambda: None)
        self.assertIsNone(result)

    def test_exception_routed_to_on_error_not_logged(self):
        seen = []
        done = threading.Event()

        def _fail():
            raise ValueError("boom")

        def on_error(failure):
            seen.append(failure)
            done.set()

        defer.background(_fail, on_error=on_error)

        deadline = time.time() + _DRAIN_TIMEOUT
        while not done.is_set() and time.time() < deadline:
            self._loop.run_until_complete(asyncio.sleep(0.05))

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].type, ValueError)

    def test_exception_logged_by_default(self):
        def _fail():
            raise ValueError("boom")

        with patch.object(defer.logger, "log_err") as mock_err:
            defer.background(_fail)

            deadline = time.time() + _DRAIN_TIMEOUT
            while mock_err.call_count == 0 and time.time() < deadline:
                self._loop.run_until_complete(asyncio.sleep(0.05))

            mock_err.assert_called_once()

    def test_failure_does_not_propagate_to_caller(self):
        def _fail():
            raise ValueError("boom")

        result = defer.background(_fail)
        self.assertIsNone(result)
