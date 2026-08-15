"""Tests for the synchronous web-worker to IO-loop bridge."""

import asyncio
import threading
from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    run_on_io_thread,
)


class WebIOThreadBridgeTest(SimpleTestCase):
    """Verify scheduling and timeout ownership for the web bridge."""

    def test_inline_without_bound_loop(self):
        """Pre-bootstrap calls execute inline."""
        self.assertEqual(run_on_io_thread(lambda: 7), 7)

    def test_worker_dispatches_through_clock_scope(self):
        """A worker schedules the callback through the engine clock API."""
        queued = []

        def call_from_thread(callback):
            queued.append(callback)

        result = []
        with (
            patch("evennia.utils.clock.is_io_thread", return_value=False),
            patch("evennia.utils.clock.get_bound_loop", return_value=object()),
            patch("evennia.utils.clock.call_from_thread", side_effect=call_from_thread),
        ):
            worker = threading.Thread(target=lambda: result.append(run_on_io_thread(lambda: 11)))
            worker.start()
            while not queued:
                pass
            queued.pop()()
            worker.join(timeout=1)

        self.assertEqual(result, [11])

    def test_timeout_before_start_cancels_callback(self):
        """A callback that never started is permanently cancelled."""
        queued = []
        called = []
        with (
            patch("evennia.utils.clock.is_io_thread", return_value=False),
            patch("evennia.utils.clock.get_bound_loop", return_value=object()),
            patch("evennia.utils.clock.call_from_thread", side_effect=queued.append),
        ):
            with self.assertRaises(IOThreadCallTimeout):
                run_on_io_thread(lambda: called.append(True), timeout=0.001)
        queued.pop()()
        self.assertEqual(called, [])

    def test_timeout_after_start_is_indeterminate(self):
        """A started callback finishes once but reports an unknown outcome."""
        started = threading.Event()
        release = threading.Event()
        queued = []
        error = []

        def callback():
            started.set()
            release.wait(timeout=1)

        def invoke():
            try:
                run_on_io_thread(callback, timeout=0.01)
            except Exception as err:
                error.append(err)

        with (
            patch("evennia.utils.clock.is_io_thread", return_value=False),
            patch("evennia.utils.clock.get_bound_loop", return_value=object()),
            patch("evennia.utils.clock.call_from_thread", side_effect=queued.append),
        ):
            worker = threading.Thread(target=invoke)
            worker.start()
            while not queued:
                pass
            callback_thread = threading.Thread(target=queued.pop())
            callback_thread.start()
            self.assertTrue(started.wait(timeout=1))
            worker.join(timeout=1)
            release.set()
            callback_thread.join(timeout=1)

        self.assertEqual(len(error), 1)
        self.assertIsInstance(error[0], IOThreadCallIndeterminate)

    def test_awaitable_result_is_rejected_and_closed(self):
        """The synchronous bridge refuses coroutine callbacks."""
        coroutine = asyncio.sleep(0)
        with self.assertRaises(TypeError):
            run_on_io_thread(lambda: coroutine)
        self.assertIsNone(coroutine.cr_frame)
