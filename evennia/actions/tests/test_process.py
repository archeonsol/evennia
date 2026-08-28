"""Tests for the Activity primitive + scheduler (CM1 movement engine additions).

An :class:`Activity` is driven by ``_drive_activity`` on the event loop. The two
suspension points are exercised natively: numeric ``yield``\\s go through
``process.clock.defer_later`` (patched to ``asyncio.sleep(0)`` so a timed step
resolves inline); awaitable ``yield``\\s use an ``asyncio.Future`` gate the test
resolves or cancels by hand to assert suspension, resume-with-value (per-step
re-validation), and cancellation. Suspend/resume tests run the driver as a task
on a live loop, ticking it forward with ``asyncio.sleep(0)``.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure

from evennia.actions import process
from evennia.actions.process import Activity
from evennia.utils import logger


def _holder():
    """A stand-in character/account: all the scheduler needs is ``.ndb``."""
    return SimpleNamespace(ndb=SimpleNamespace())


def _messaging_holder():
    """A holder that can receive scheduler failure feedback."""
    return SimpleNamespace(ndb=SimpleNamespace(), msg=mock.Mock())


# --- test activities --------------------------------------------------------
class Counter(Activity):
    """Yields ``steps`` timed pauses, recording each; completes normally."""

    key = "counter"

    def __init__(self, log, steps=2):
        super().__init__()
        self.log = log
        self.steps = steps

    def run(self):
        for i in range(self.steps):
            self.log.append(f"step{i}")
            yield 0.1
        self.log.append("ran")

    def on_complete(self):
        self.log.append("complete")

    def on_cancel(self, reason=None):
        self.log.append(f"cancel:{reason}")


class Waiter(Activity):
    """Suspends on an externally-controlled ``asyncio.Future``, then continues."""

    key = "waiter"
    exclusive_group = "exclusive"

    def __init__(self, log, name, gate):
        super().__init__()
        self.log = log
        self.name = name
        self.gate = gate

    def run(self):
        self.log.append(f"start:{self.name}")
        result = yield self.gate
        self.log.append(f"resume:{self.name}:{result}")

    def on_complete(self):
        self.log.append(f"complete:{self.name}")

    def on_cancel(self, reason=None):
        self.log.append(f"cancel:{self.name}:{reason}")


class Crasher(Activity):
    key = "crasher"

    def run(self):
        self.log.append("boom")
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    def __init__(self, log):
        super().__init__()
        self.log = log


class ErrorAwareActivity(Activity):
    """Activity test base recording the terminal error hook."""

    def __init__(self, actor=None):
        super().__init__(actor)
        self.errors = []

    def on_error(self, error):
        self.errors.append(error)


class ConstructionCrasher(ErrorAwareActivity):
    """Raise before returning a generator object."""

    key = "construction-crasher"

    def run(self):
        raise RuntimeError("construction kaboom")


class NonGeneratorActivity(ErrorAwareActivity):
    """Return an invalid non-generator body."""

    key = "non-generator"

    def run(self):
        return None


class StepCrasher(ErrorAwareActivity):
    """Raise while advancing the initial generator step."""

    key = "step-crasher"

    def run(self):
        raise RuntimeError("step kaboom")
        yield  # pragma: no cover


class SuspendedCrasher(ErrorAwareActivity):
    """Raise after a successful suspension resumes."""

    key = "suspended-crasher"

    def __init__(self, gate, actor=None):
        super().__init__(actor)
        self.gate = gate

    def run(self):
        yield self.gate
        raise RuntimeError("resume kaboom")


# --- lifecycle --------------------------------------------------------------
class TestActivityLifecycle(unittest.TestCase):
    def test_runs_to_completion_and_unregisters(self):
        holder, log = _holder(), []
        activity = Counter(log, steps=2)
        activity.on_error = mock.Mock()
        with mock.patch.object(
            process.clock, "defer_later", side_effect=lambda *a, **k: asyncio.sleep(0)
        ):
            process.start_activity(holder, activity)
        self.assertEqual(log, ["step0", "step1", "ran", "complete"])
        self.assertEqual(process.get_activities(holder), [])
        self.assertFalse(process.is_active(holder, "counter"))
        self.assertFalse(activity.running)
        activity.on_error.assert_not_called()

    def test_crashing_body_is_contained(self):
        holder, log = _holder(), []
        with mock.patch.object(logger, "log_trace"):
            process.start_activity(holder, Crasher(log))
        self.assertEqual(log, ["boom"])
        self.assertEqual(process.get_activities(holder), [])

    @override_settings(IN_GAME_ERRORS=False)
    def test_run_construction_crash_notifies_and_finishes(self):
        holder = _messaging_holder()
        activity = ConstructionCrasher()

        with mock.patch.object(logger, "log_trace") as log_trace:
            process.start_activity(holder, activity)

        log_trace.assert_called_once()
        holder.msg.assert_called_once()
        rendered = holder.msg.call_args.args[0]
        self.assertIn("untrapped error", rendered.lower())
        self.assertNotIn("construction kaboom", rendered)
        self.assertEqual(len(activity.errors), 1)
        self.assertIsInstance(activity.errors[0], RuntimeError)
        self.assertEqual(process.get_activities(holder), [])
        self.assertFalse(activity.running)

    @override_settings(IN_GAME_ERRORS=True)
    def test_initial_generator_crash_includes_development_traceback(self):
        holder = _messaging_holder()
        activity = StepCrasher()

        with mock.patch.object(logger, "log_trace"):
            process.start_activity(holder, activity)

        rendered = holder.msg.call_args.args[0]
        self.assertIn("RuntimeError: step kaboom", rendered)
        self.assertEqual(len(activity.errors), 1)
        self.assertIsInstance(activity.errors[0], RuntimeError)
        self.assertFalse(activity.running)

    def test_non_generator_body_uses_error_terminal(self):
        holder = _messaging_holder()
        activity = NonGeneratorActivity()

        with mock.patch.object(logger, "log_trace"):
            process.start_activity(holder, activity)

        holder.msg.assert_called_once()
        self.assertEqual(len(activity.errors), 1)
        self.assertIsInstance(activity.errors[0], AttributeError)
        self.assertFalse(activity.running)

    def test_error_delivery_prefers_actor_without_duplicate_holder_message(self):
        holder = _messaging_holder()
        actor = SimpleNamespace(msg=mock.Mock())
        activity = StepCrasher(actor=actor)

        with mock.patch.object(logger, "log_trace"):
            process.start_activity(holder, activity)

        actor.msg.assert_called_once()
        holder.msg.assert_not_called()

    def test_error_delivery_deduplicates_actor_holder_alias(self):
        holder = _messaging_holder()
        activity = StepCrasher(actor=holder)

        with mock.patch.object(logger, "log_trace"):
            process.start_activity(holder, activity)

        holder.msg.assert_called_once()

    def test_raising_actor_message_falls_back_to_holder(self):
        holder = _messaging_holder()
        actor = SimpleNamespace(msg=mock.Mock(side_effect=RuntimeError("send failed")))
        activity = StepCrasher(actor=actor)

        with mock.patch.object(logger, "log_trace") as log_trace:
            process.start_activity(holder, activity)

        actor.msg.assert_called_once()
        holder.msg.assert_called_once()
        self.assertGreaterEqual(log_trace.call_count, 2)

    def test_crashing_error_hook_cannot_suppress_notification(self):
        holder = _messaging_holder()

        class BrokenHook(StepCrasher):
            def on_error(self, error):
                raise RuntimeError("hook failed")

        with mock.patch.object(logger, "log_trace") as log_trace:
            process.start_activity(holder, BrokenHook())

        holder.msg.assert_called_once()
        self.assertGreaterEqual(log_trace.call_count, 2)

    def test_finished_activity_instance_is_single_use(self):
        holder = _messaging_holder()
        activity = StepCrasher()
        with mock.patch.object(logger, "log_trace"):
            process.start_activity(holder, activity)

        with self.assertRaisesRegex(RuntimeError, "single-use"):
            process.start_activity(holder, activity)
        self.assertEqual(process.get_activities(holder), [])


class _LoopActivityTest(unittest.TestCase):
    """Base for tests that need a live loop to drive suspensions.

    The body's ``yield`` gate is an ``asyncio.Future`` the test resolves or
    cancels while the driver runs as a task on ``self._loop``; ``_tick`` steps
    the loop forward so a resolve/cancel unwinds the driver.
    """

    def setUp(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()
        asyncio.set_event_loop(None)

    def _gate(self):
        return self._loop.create_future()

    async def _tick(self, n=3):
        for _ in range(n):
            await asyncio.sleep(0)

    def _run(self, coro):
        return self._loop.run_until_complete(coro)


class TestActivitySuspend(_LoopActivityTest):
    def test_registered_and_queryable_while_suspended(self):
        holder, log = _holder(), []

        async def scenario():
            act = Waiter(log, "a", self._gate())
            process.start_activity(holder, act)
            await self._tick()
            self.assertEqual(log, ["start:a"])
            self.assertTrue(process.is_active(holder, "waiter"))
            self.assertTrue(process.is_active(holder, "exclusive"))  # by group
            self.assertIs(process.active_activity(holder, "waiter"), act)
            self.assertTrue(act.running)
            act.cancel()  # cleanup: let the driver finish before the loop closes
            await self._tick()

        self._run(scenario())

    def test_gate_yield_resumes_with_value(self):
        # The per-step re-validation contract: yield an awaitable (engine.dispatch),
        # resume with its resolved value.
        holder, log = _holder(), []

        async def scenario():
            gate = self._gate()
            process.start_activity(holder, Waiter(log, "a", gate))
            await self._tick()
            self.assertEqual(log, ["start:a"])
            gate.set_result("TRACE")
            await self._tick()
            self.assertEqual(log, ["start:a", "resume:a:TRACE", "complete:a"])
            self.assertEqual(process.get_activities(holder), [])

        self._run(scenario())

    def test_pending_deferred_resumes_under_native_asyncio_driver(self):
        holder, log = _holder(), []

        async def scenario():
            gate = Deferred()
            process.start_activity(holder, Waiter(log, "legacy", gate))
            await self._tick()
            self.assertEqual(log, ["start:legacy"])
            gate.callback("TRACE")
            await self._tick()

        self._run(scenario())
        self.assertEqual(log, ["start:legacy", "resume:legacy:TRACE", "complete:legacy"])
        self.assertEqual(process.get_activities(holder), [])

    def test_post_suspension_generator_crash_notifies_once(self):
        holder = _messaging_holder()

        async def scenario():
            gate = self._gate()
            activity = SuspendedCrasher(gate)
            with mock.patch.object(logger, "log_trace") as log_trace:
                process.start_activity(holder, activity)
                await self._tick()
                gate.set_result(None)
                await self._tick()
            self.assertEqual(log_trace.call_count, 1)
            holder.msg.assert_called_once()
            self.assertEqual(len(activity.errors), 1)
            self.assertIn("resume kaboom", str(activity.errors[0]))
            self.assertEqual(process.get_activities(holder), [])
            self.assertFalse(activity.running)

        self._run(scenario())

    def test_future_failure_uses_single_error_terminal(self):
        holder = _messaging_holder()

        async def scenario():
            gate = self._gate()
            activity = SuspendedCrasher(gate)
            with mock.patch.object(logger, "log_trace") as log_trace:
                process.start_activity(holder, activity)
                await self._tick()
                gate.set_exception(RuntimeError("future kaboom"))
                await self._tick()
            self.assertEqual(log_trace.call_count, 1)
            holder.msg.assert_called_once()
            self.assertEqual(len(activity.errors), 1)
            self.assertIn("future kaboom", str(activity.errors[0]))
            self.assertEqual(process.get_activities(holder), [])

        self._run(scenario())

    def test_deferred_failure_uses_single_error_terminal(self):
        holder = _messaging_holder()

        async def scenario():
            gate = Deferred()
            activity = SuspendedCrasher(gate)
            with mock.patch.object(logger, "log_trace") as log_trace:
                process.start_activity(holder, activity)
                await self._tick()
                gate.errback(Failure(RuntimeError("deferred kaboom")))
                await self._tick()
            self.assertEqual(log_trace.call_count, 1)
            holder.msg.assert_called_once()
            self.assertEqual(len(activity.errors), 1)
            self.assertIn("deferred kaboom", str(activity.errors[0]))
            self.assertEqual(process.get_activities(holder), [])

        self._run(scenario())

    def test_active_activity_instance_is_single_use(self):
        holder, log = _holder(), []

        async def scenario():
            activity = Waiter(log, "a", self._gate())
            process.start_activity(holder, activity)
            await self._tick()
            with self.assertRaisesRegex(RuntimeError, "single-use"):
                process.start_activity(holder, activity)
            self.assertEqual(process.get_activities(holder), [activity])
            activity.cancel()
            await self._tick()

        self._run(scenario())


# --- cancellation -----------------------------------------------------------
class TestActivityCancel(_LoopActivityTest):
    def test_cancel_while_suspended_on_deferred(self):
        holder, log = _holder(), []

        async def scenario():
            gate = Deferred()
            process.start_activity(holder, Waiter(log, "legacy", gate))
            await self._tick()
            process.cancel_activity(holder, "waiter", reason="stop")
            await self._tick()
            self.assertTrue(gate.called)
            self.assertEqual(log, ["start:legacy", "cancel:legacy:stop"])
            self.assertFalse(process.is_active(holder, "waiter"))

        self._run(scenario())

    def test_cancel_while_suspended_runs_on_cancel(self):
        holder, log = _holder(), []

        async def scenario():
            activity = Waiter(log, "a", self._gate())
            activity.on_error = mock.Mock()
            process.start_activity(holder, activity)
            await self._tick()
            process.cancel_activity(holder, "waiter", reason="stop")
            await self._tick()
            self.assertEqual(log, ["start:a", "cancel:a:stop"])
            self.assertFalse(process.is_active(holder, "waiter"))
            self.assertFalse(activity.running)
            activity.on_error.assert_not_called()

        self._run(scenario())

    def test_cancel_by_group(self):
        holder, log = _holder(), []

        async def scenario():
            process.start_activity(holder, Waiter(log, "a", self._gate()))
            await self._tick()
            cancelled = process.cancel_activity(holder, "exclusive", reason="grp")
            await self._tick()
            self.assertEqual(len(cancelled), 1)
            self.assertEqual(log, ["start:a", "cancel:a:grp"])

        self._run(scenario())

    def test_cancel_by_instance(self):
        holder, log = _holder(), []

        async def scenario():
            act = Waiter(log, "a", self._gate())
            process.start_activity(holder, act)
            await self._tick()
            process.cancel_activity(holder, act, reason="inst")
            await self._tick()
            self.assertEqual(log, ["start:a", "cancel:a:inst"])

        self._run(scenario())

    def test_cancel_is_idempotent(self):
        holder, log = _holder(), []

        async def scenario():
            act = Waiter(log, "a", self._gate())
            process.start_activity(holder, act)
            await self._tick()
            process.cancel_activity(holder, "waiter", reason="first")
            process.cancel_activity(holder, "waiter", reason="second")  # no-op
            await self._tick()
            self.assertEqual(log, ["start:a", "cancel:a:first"])

        self._run(scenario())

    def test_cancelled_body_does_not_resume(self):
        holder, log = _holder(), []

        async def scenario():
            act = Waiter(log, "a", self._gate())
            process.start_activity(holder, act)
            await self._tick()
            act.cancel(reason="x")  # also cancels the in-flight gate
            await self._tick()
            self.assertNotIn("resume:a:None", log)

        self._run(scenario())


# --- exclusivity ------------------------------------------------------------
class TestActivityExclusivity(_LoopActivityTest):
    def test_same_group_supersedes(self):
        holder, log = _holder(), []

        async def scenario():
            a1 = Waiter(log, "a1", self._gate())
            a2 = Waiter(log, "a2", self._gate())
            process.start_activity(holder, a1)
            await self._tick()
            process.start_activity(holder, a2)  # same exclusive_group → cancels a1
            await self._tick()
            self.assertIn("cancel:a1:superseded", log)
            self.assertIs(process.active_activity(holder, "waiter"), a2)
            self.assertEqual(len(process.get_activities(holder)), 1)
            a2.cancel()  # cleanup
            await self._tick()

        self._run(scenario())

    def test_distinct_keys_and_groups_coexist(self):
        holder, log = _holder(), []

        class Other(Waiter):
            key = "other"
            exclusive_group = None

        async def scenario():
            process.start_activity(holder, Waiter(log, "w", self._gate()))
            process.start_activity(holder, Other(log, "o", self._gate()))
            await self._tick()
            self.assertEqual(len(process.get_activities(holder)), 2)
            for act in list(process.get_activities(holder)):
                act.cancel()  # cleanup
            await self._tick()

        self._run(scenario())


# --- synchronous prefix (inlineCallbacks-parity) ----------------------------
class TestActivitySyncPrefix(_LoopActivityTest):
    """The body's synchronous prefix runs inline before start_activity returns."""

    def test_prefix_without_suspension_runs_before_start_returns(self):
        holder, events = _holder(), []

        class NoSuspend(Activity):
            key = "nosuspend"

            def run(self):
                events.append("body")
                return
                yield  # pragma: no cover - generator marker

        async def scenario():
            process.start_activity(holder, NoSuspend())
            # the body must already have run, before we yield to the loop
            return list(events)

        self.assertEqual(self._run(scenario()), ["body"])

    def test_prefix_before_first_suspension_runs_inline(self):
        holder, events = _holder(), []

        async def scenario():
            gate = self._gate()

            class MsgThenWait(Activity):
                key = "msgwait"

                def run(self):
                    events.append("before")
                    yield gate  # first real suspension
                    events.append("after")

            process.start_activity(holder, MsgThenWait())
            inline = list(events)  # observed after start_activity, before awaiting
            gate.set_result(None)
            await self._tick()
            return inline, list(events)

        inline, final = self._run(scenario())
        self.assertEqual(inline, ["before"])  # prefix ran inline, not next tick
        self.assertEqual(final, ["before", "after"])  # remainder ran on the loop


# --- cancellation on the real asyncio path ---------------------------------
class Sleeper(Activity):
    """Suspends on a real ``clock.defer_later`` sleep (an ``asyncio.Task``)."""

    key = "sleeper"

    def __init__(self, log):
        super().__init__()
        self.log = log

    def run(self):
        self.log.append("start")
        yield 10  # real defer_later sleep -> asyncio.Task in self._pending
        self.log.append("resumed")  # must NOT run after a mid-suspend cancel

    def on_cancel(self, reason=None):
        self.log.append(f"cancel:{reason}")


class TestActivityCancelAsyncio(unittest.TestCase):
    """Cancellation must work when ``_pending`` is a real asyncio object.

    The legacy cancel tests drive raw Twisted ``Deferred``s (``.called`` +
    twisted ``CancelledError``); production suspension points hold an
    ``asyncio.Task`` from ``clock.defer_later``, which has neither.
    """

    def setUp(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()
        asyncio.set_event_loop(None)

    def test_cancel_during_real_defer_later_sleep(self):
        holder, log = _holder(), []

        async def scenario():
            process.start_activity(holder, Sleeper(log))
            await asyncio.sleep(0)  # let the driver reach the defer_later await
            act = process.active_activity(holder, "sleeper")
            self.assertIsNotNone(act)
            act.cancel(reason="stop")
            await asyncio.sleep(0)  # let the cancellation unwind the driver

        self._loop.run_until_complete(scenario())
        self.assertIn("start", log)
        self.assertIn("cancel:stop", log)  # on_cancel fired
        self.assertNotIn("resumed", log)  # body did not resume
        self.assertFalse(process.is_active(holder, "sleeper"))


if __name__ == "__main__":
    unittest.main()
