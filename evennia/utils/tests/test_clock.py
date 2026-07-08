"""
Tests for evennia.utils.clock scheduling backstops.

Focus: unhandled errors on scheduled coroutines and repeating loops must reach
the log with a full traceback (frame info), not a bare ``str(exc)`` line. A
fresh asyncio loop is installed per test; the sync-drive path is exercised with
no running loop.
"""

import asyncio
from unittest.mock import patch

from evennia.utils import clock
from evennia.utils.test_resources import BaseEvenniaTestCase


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


def _distinctively_named_boom():
    raise ValueError("kaboom")


class TestRunCoroutineBackstop(_AsyncioLoopMixin, BaseEvenniaTestCase):
    """`run_coroutine`'s done-callback logs the full traceback, not str(exc)."""

    def test_running_loop_path_logs_traceback(self):
        captured = []

        async def boom():
            _distinctively_named_boom()

        async def drive():
            task = clock.run_coroutine(boom())
            try:
                await task
            except ValueError:
                pass
            # flush the done-callback
            await asyncio.sleep(0)

        with patch("evennia.utils.logger.log_err", captured.append):
            self._loop.run_until_complete(drive())

        msg = "\n".join(captured)
        self.assertIn("kaboom", msg)
        self.assertIn("_distinctively_named_boom", msg)  # frame proves stack captured
        self.assertIn("Traceback", msg)

    def test_sync_drive_path_logs_traceback(self):
        captured = []

        async def boom():
            _distinctively_named_boom()

        # No running loop -> _SyncCoroutineResult path.
        with patch("evennia.utils.logger.log_err", captured.append):
            clock.run_coroutine(boom())

        msg = "\n".join(captured)
        self.assertIn("kaboom", msg)
        self.assertIn("_distinctively_named_boom", msg)
        self.assertIn("Traceback", msg)


class TestLoopHandleBackstop(_AsyncioLoopMixin, BaseEvenniaTestCase):
    """A repeating loop whose body raises must not stop silently."""

    def test_body_error_without_on_error_is_logged(self):
        captured = []

        def body():
            _distinctively_named_boom()

        async def drive():
            handle = clock.looping(0.001, body, now=True)
            await asyncio.sleep(0.02)
            handle.stop()

        with patch("evennia.utils.logger.log_trace", lambda *a, **k: captured.append(a)):
            self._loop.run_until_complete(drive())

        self.assertTrue(captured, "loop body error was swallowed silently")


class TestDeferLaterCompatErrbacks(_AsyncioLoopMixin, BaseEvenniaTestCase):
    """`_DeferLaterCompat._run_errbacks` must not UnboundLocalError, double-call, or swallow."""

    def _make(self):
        d = clock._DeferLaterCompat(0, lambda: None)
        self.addCleanup(d.cancel)
        return d

    def test_errback_receives_failure(self):
        from twisted.python.failure import Failure

        got = []
        d = self._make()
        d.addErrback(got.append)
        d._run_errbacks(ValueError("boom"))
        self.assertEqual(len(got), 1)
        self.assertIsInstance(got[0], Failure)
        self.assertIsInstance(got[0].value, ValueError)

    def test_raising_errback_invoked_once_and_propagates_real_error(self):
        calls = []
        sentinel = RuntimeError("errback-boom")

        def erroring_errback(failure):
            calls.append(failure)
            raise sentinel

        d = self._make()
        d.addErrback(erroring_errback)
        with self.assertRaises(RuntimeError) as ctx:
            d._run_errbacks(ValueError("orig"))
        self.assertIs(ctx.exception, sentinel)  # not UnboundLocalError
        self.assertEqual(len(calls), 1)  # not double-invoked


class TestDefaultExecutorLifecycle(BaseEvenniaTestCase):
    """The shared worker pool is built once and shut down on graceful exit."""

    def setUp(self):
        super().setUp()
        self._saved_executor = clock._default_executor
        self._saved_loop = clock._main_loop
        self._saved_hooks = clock._shutdown_hooks[:]
        clock._default_executor = None
        clock._shutdown_hooks.clear()

    def tearDown(self):
        clock.shutdown_default_executor()
        clock._default_executor = self._saved_executor
        clock._main_loop = self._saved_loop
        clock._shutdown_hooks[:] = self._saved_hooks
        super().tearDown()

    def test_bind_loop_constructs_executor_eagerly(self):
        loop = asyncio.new_event_loop()
        try:
            self.assertIsNone(clock._default_executor)
            clock.bind_loop(loop)
            # eager build in the single-threaded bootstrap → no lazy construct race
            self.assertIsNotNone(clock._default_executor)
        finally:
            loop.close()

    def test_run_shutdown_hooks_shuts_down_executor(self):
        loop = asyncio.new_event_loop()
        try:
            clock.bind_loop(loop)
            executor = clock._default_executor
            self.assertIsNotNone(executor)

            clock.run_shutdown_hooks()

            self.assertIsNone(clock._default_executor)
            with self.assertRaises(RuntimeError):
                executor.submit(lambda: None)  # pool is shut down, not leaked
        finally:
            loop.close()
