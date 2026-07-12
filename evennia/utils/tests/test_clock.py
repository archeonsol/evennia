"""
Tests for evennia.utils.clock scheduling backstops.

Focus: unhandled errors on scheduled coroutines and repeating loops must reach
the log with a full traceback (frame info), not a bare ``str(exc)`` line. A
fresh asyncio loop is installed per test; the sync-drive path is exercised with
no running loop.
"""

import asyncio
import contextvars
import threading
from unittest.mock import MagicMock, patch

from django.db import connections
from django.test import SimpleTestCase

from evennia.utils import clock


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


class TestRunCoroutineBackstop(_AsyncioLoopMixin, SimpleTestCase):
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


class TestLoopHandleBackstop(_AsyncioLoopMixin, SimpleTestCase):
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


class TestDeferLaterCompatErrbacks(_AsyncioLoopMixin, SimpleTestCase):
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


class TestDefaultExecutorLifecycle(SimpleTestCase):
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


class TestClockLoopBinding(SimpleTestCase):
    """bind_loop is the sole authority for the bound loop + its owning thread."""

    def setUp(self):
        super().setUp()
        self._saved_loop = clock._main_loop
        self._saved_tid = clock._loop_thread_id

    def tearDown(self):
        clock._main_loop = self._saved_loop
        clock._loop_thread_id = self._saved_tid
        super().tearDown()

    def test_get_loop_does_not_rebind_main_loop(self):
        bound = asyncio.new_event_loop()
        other = asyncio.new_event_loop()
        try:
            clock.bind_loop(bound)

            async def _inside():
                return clock._get_loop()

            got = other.run_until_complete(_inside())
            self.assertIs(got, other)  # returns the actually-running loop
            self.assertIs(clock._main_loop, bound)  # but must not rebind the global
        finally:
            bound.close()
            other.close()

    def test_is_io_thread_uses_recorded_thread_ident(self):
        import threading

        loop = asyncio.new_event_loop()
        try:
            clock.bind_loop(loop)
            # binding thread, no running loop → identified via recorded ident
            self.assertTrue(clock.is_io_thread())

            result = {}

            def worker():
                result["io"] = clock.is_io_thread()

            t = threading.Thread(target=worker)
            t.start()
            t.join()
            self.assertFalse(result["io"])
        finally:
            loop.close()


class TestFixedRateSchedule(SimpleTestCase):
    """LoopHandle cadence is fixed-rate (anchored), not fixed-delay."""

    def test_fast_body_keeps_interval(self):
        # body finished at now=1, interval=5, prev target=0 → next slot at 5
        self.assertEqual(clock._next_fire_time(0.0, 5.0, 1.0), 5.0)

    def test_overrun_one_interval_skips_to_next_future_slot(self):
        # body ran past the 5 slot to now=7 → next future slot 10, no bunching
        self.assertEqual(clock._next_fire_time(0.0, 5.0, 7.0), 10.0)

    def test_overrun_many_intervals_skips_all_missed(self):
        self.assertEqual(clock._next_fire_time(0.0, 5.0, 17.0), 20.0)


class TestSyncCoroutineResultDrain(SimpleTestCase):
    """The no-running-loop path must not orphan child tasks on loop close."""

    def test_child_tasks_are_cancelled_not_orphaned(self):
        captured = {}

        async def child():
            await asyncio.sleep(100)

        async def parent():
            captured["task"] = asyncio.ensure_future(child())  # fire-and-forget child
            return "done"

        res = clock.run_coroutine(parent())  # no running loop → _SyncCoroutineResult
        self.assertEqual(res.result(), "done")
        # The driver steps the parent body off a running loop (to keep ORM off a
        # run-loop context), so a never-awaited child is drained by cancellation
        # rather than run first. The contract is that it is not left pending
        # ("Task was destroyed but it is pending"): it must be settled.
        task = captured["task"]
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())


class TestCallFromThread(_AsyncioLoopMixin, SimpleTestCase):
    """`call_from_thread` hands work from a worker thread onto the loop thread.

    Everywhere else this primitive is exercised through a sync stub (redis bus
    tests) or a `call_soon_threadsafe` mock; here a real `threading.Thread`
    proves the callable actually runs on the loop thread, not the caller's.
    """

    def setUp(self):
        super().setUp()
        # bind_loop is the sole writer of the module-global loop/thread identity;
        # snapshot and restore it so this test does not bleed into others.
        self._saved = (clock._main_loop, clock._loop_thread_id)
        clock.bind_loop(self._loop)  # records this (loop-owning) thread's ident

    def tearDown(self):
        clock.shutdown_default_executor()  # bind_loop built the pool eagerly
        clock._main_loop, clock._loop_thread_id = self._saved
        super().tearDown()

    def test_callable_runs_on_loop_thread_not_worker(self):
        ran = {}

        def target():
            ran["thread"] = threading.get_ident()

        async def drive():
            loop_ident = threading.get_ident()  # the coro runs on the loop thread
            worker_ident = {}

            def worker():
                worker_ident["id"] = threading.get_ident()
                clock.call_from_thread(target)

            t = threading.Thread(target=worker)
            t.start()
            t.join()
            for _ in range(5):  # let the loop process the thread-safe callback
                await asyncio.sleep(0)
            return loop_ident, worker_ident["id"]

        loop_ident, worker_ident = self._loop.run_until_complete(drive())
        self.assertNotEqual(loop_ident, worker_ident)  # genuinely two threads
        self.assertEqual(ran.get("thread"), loop_ident)  # ran on loop, not worker

    def test_args_and_kwargs_are_forwarded(self):
        # kwargs takes the functools.partial branch; args-only takes the other.
        got = {}

        def target(a, b=None):
            got["a"], got["b"] = a, b

        async def drive():
            def worker():
                clock.call_from_thread(target, "pos", b="kw")

            t = threading.Thread(target=worker)
            t.start()
            t.join()
            for _ in range(5):
                await asyncio.sleep(0)

        self._loop.run_until_complete(drive())
        self.assertEqual((got.get("a"), got.get("b")), ("pos", "kw"))


class TestRuntimeCallbackDatabaseScope(_AsyncioLoopMixin, SimpleTestCase):
    """Timer and completion callbacks cannot retain inherited DB wrappers."""

    def test_call_later_closes_callback_database_scope(self):
        close_all = MagicMock()
        called = []

        async def drive():
            with patch.object(connections, "close_all", close_all):
                clock.call_later(0, called.append, "done")
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        self._loop.run_until_complete(drive())
        self.assertEqual(called, ["done"])
        close_all.assert_called_once_with()

    def test_run_callback_detaches_parent_wrapper(self):
        async def drive():
            parent = connections["default"]
            observed = clock.run_callback(lambda: connections["default"])
            return parent, observed

        parent, observed = self._loop.run_until_complete(drive())
        self.assertIsNot(parent, observed)


class TestRunCoroutineDirect(_AsyncioLoopMixin, SimpleTestCase):
    """`run_coroutine` on a running loop returns a Task that carries the result."""

    def test_running_loop_returns_task_with_result(self):
        async def compute():
            await asyncio.sleep(0)
            return 42

        async def drive():
            task = clock.run_coroutine(compute())
            self.assertIsInstance(task, asyncio.Task)
            return await task

        self.assertEqual(self._loop.run_until_complete(drive()), 42)


class TestRuntimeTaskDatabaseScope(_AsyncioLoopMixin, SimpleTestCase):
    """Detached roots own isolated Django connection state and always release it."""

    def test_root_detaches_database_wrapper_but_preserves_other_context(self):
        marker = contextvars.ContextVar("clock_test_marker", default="missing")

        async def drive():
            parent_wrapper = connections["default"]
            marker.set("trace-123")
            observed = {}

            async def root():
                observed["wrapper"] = connections["default"]
                observed["marker"] = marker.get()

            await clock.run_coroutine(root(), task_kind="test")
            return parent_wrapper, observed

        parent_wrapper, observed = self._loop.run_until_complete(drive())
        self.assertIsNot(observed["wrapper"], parent_wrapper)
        self.assertEqual(observed["marker"], "trace-123")

    def test_root_closes_its_database_scope_on_success(self):
        close_all = MagicMock()

        async def drive():
            with patch.object(connections, "close_all", close_all):
                await clock.run_coroutine(asyncio.sleep(0), task_kind="test")

        self._loop.run_until_complete(drive())
        close_all.assert_called_once_with()

    def test_root_closes_its_database_scope_on_cancellation(self):
        close_all = MagicMock()

        async def wait_forever():
            await asyncio.Event().wait()

        async def drive():
            with patch.object(connections, "close_all", close_all):
                task = clock.run_coroutine(wait_forever(), task_kind="test")
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        self._loop.run_until_complete(drive())
        close_all.assert_called_once_with()

    def test_root_closes_its_database_scope_on_failure(self):
        close_all = MagicMock()

        async def fail():
            raise ValueError("root failed")

        async def drive():
            with patch.object(connections, "close_all", close_all):
                task = clock.run_coroutine(fail(), task_kind="test")
                with self.assertRaises(ValueError):
                    await task
                await asyncio.sleep(0)

        self._loop.run_until_complete(drive())
        close_all.assert_called_once_with()

    def test_many_roots_do_not_accumulate_database_wrappers(self):
        closers = []

        async def drive():
            parent_wrapper = connections["default"]
            for _ in range(250):

                async def root():
                    wrapper = connections["default"]
                    self.assertIsNot(wrapper, parent_wrapper)
                    closer = MagicMock()
                    wrapper.close = closer
                    closers.append(closer)

                await clock.run_coroutine(root(), task_kind="test")

        self._loop.run_until_complete(drive())
        self.assertEqual(len(closers), 250)
        self.assertTrue(all(closer.call_count == 1 for closer in closers))

    def test_cancel_before_first_step_closes_user_coroutine(self):
        async def never_started():
            await asyncio.sleep(1)

        async def drive():
            coro = never_started()
            task = clock.run_coroutine(coro, task_kind="test")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            return coro

        coro = self._loop.run_until_complete(drive())
        self.assertIsNone(coro.cr_frame)


class TestLoopHandleDirect(_AsyncioLoopMixin, SimpleTestCase):
    """`LoopHandle` fires repeatedly until stopped, then fires no more."""

    def test_repeats_then_stop_halts_further_fires(self):
        calls = []

        async def drive():
            handle = clock.looping(0.001, calls.append, "x", now=True)
            while len(calls) < 3:  # wait for several real fires
                await asyncio.sleep(0.001)
            handle.stop()
            n_at_stop = len(calls)
            self.assertFalse(handle.running)  # stop() clears the flag at once
            await asyncio.sleep(0.02)  # window in which a stray fire would show
            return n_at_stop

        n_at_stop = self._loop.run_until_complete(drive())
        self.assertGreaterEqual(n_at_stop, 3)
        self.assertEqual(len(calls), n_at_stop)  # nothing fired after stop()
        self.assertTrue(all(c == "x" for c in calls))  # args forwarded each fire


class TestDeferLaterCompatPauseCancel(_AsyncioLoopMixin, SimpleTestCase):
    """`_DeferLaterCompat` pause/cancel semantics (errbacks covered elsewhere)."""

    def _make(self, fn):
        d = clock._DeferLaterCompat(0, fn)
        self.addCleanup(d.cancel)
        return d

    def test_pause_defers_fire_until_unpause(self):
        ran = []
        d = self._make(lambda: ran.append(1))
        d.pause()
        d._fire()  # the scheduled fire arrives while paused
        self.assertFalse(d.called)
        self.assertEqual(ran, [])
        d.unpause()  # deferred fire runs now
        self.assertTrue(d.called)
        self.assertEqual(ran, [1])

    def test_cancel_before_fire_prevents_invocation(self):
        ran = []
        d = self._make(lambda: ran.append(1))
        d.cancel()
        d._fire()  # cancelled → no-op
        self.assertFalse(d.called)
        self.assertEqual(ran, [])

    def test_cancel_after_fire_is_noop(self):
        ran = []
        d = self._make(lambda: ran.append(1))
        d._fire()  # fires now
        self.assertTrue(d.called)
        d.cancel()  # guarded by `if self.called`; must not re-run or raise
        self.assertEqual(ran, [1])
