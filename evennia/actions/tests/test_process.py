"""Tests for the Activity primitive + scheduler (CM1 movement engine additions).

An :class:`Activity` is driven by ``_drive_activity`` (an ``inlineCallbacks``
generator) on the reactor. To keep these tests synchronous we control the two
suspension points: numeric ``yield``\\s go through ``process.deferLater``, which
we patch to ``succeed(None)`` so a timed step resolves inline; ``Deferred``
``yield``\\s use bare ``Deferred``\\s we fire (or cancel) by hand to assert
suspension, resume-with-value (per-step re-validation), and cancellation.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from twisted.internet.defer import Deferred, succeed

from evennia.actions import process
from evennia.actions.process import Activity
from evennia.utils import logger


def _holder():
    """A stand-in character/account: all the scheduler needs is ``.ndb``."""
    return SimpleNamespace(ndb=SimpleNamespace())


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
    """Suspends on an externally-controlled Deferred, then continues."""

    key = "waiter"
    exclusive_group = "exclusive"

    def __init__(self, log, name, deferred):
        super().__init__()
        self.log = log
        self.name = name
        self.deferred = deferred

    def run(self):
        self.log.append(f"start:{self.name}")
        result = yield self.deferred
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


# --- lifecycle --------------------------------------------------------------
class TestActivityLifecycle(unittest.TestCase):
    def test_runs_to_completion_and_unregisters(self):
        holder, log = _holder(), []
        with mock.patch.object(process, "deferLater", return_value=succeed(None)):
            process.start_activity(holder, Counter(log, steps=2))
        self.assertEqual(log, ["step0", "step1", "ran", "complete"])
        self.assertEqual(process.get_activities(holder), [])
        self.assertFalse(process.is_active(holder, "counter"))

    def test_registered_and_queryable_while_suspended(self):
        holder, log = _holder(), []
        act = Waiter(log, "a", Deferred())
        process.start_activity(holder, act)
        self.assertEqual(log, ["start:a"])
        self.assertTrue(process.is_active(holder, "waiter"))
        self.assertTrue(process.is_active(holder, "exclusive"))  # by group
        self.assertIs(process.active_activity(holder, "waiter"), act)
        self.assertTrue(act.running)

    def test_deferred_yield_resumes_with_value(self):
        # The per-step re-validation contract: yield a Deferred (engine.dispatch),
        # resume with its resolved value.
        holder, log = _holder(), []
        d = Deferred()
        process.start_activity(holder, Waiter(log, "a", d))
        self.assertEqual(log, ["start:a"])
        d.callback("TRACE")
        self.assertEqual(log, ["start:a", "resume:a:TRACE", "complete:a"])
        self.assertEqual(process.get_activities(holder), [])

    def test_crashing_body_is_contained(self):
        holder, log = _holder(), []
        with mock.patch.object(logger, "log_trace"):
            process.start_activity(holder, Crasher(log))
        self.assertEqual(log, ["boom"])
        self.assertEqual(process.get_activities(holder), [])


# --- cancellation -----------------------------------------------------------
class TestActivityCancel(unittest.TestCase):
    def test_cancel_while_suspended_runs_on_cancel(self):
        holder, log = _holder(), []
        d = Deferred()
        process.start_activity(holder, Waiter(log, "a", d))
        process.cancel_activity(holder, "waiter", reason="stop")
        self.assertEqual(log, ["start:a", "cancel:a:stop"])
        self.assertFalse(process.is_active(holder, "waiter"))

    def test_cancel_by_group(self):
        holder, log = _holder(), []
        process.start_activity(holder, Waiter(log, "a", Deferred()))
        cancelled = process.cancel_activity(holder, "exclusive", reason="grp")
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(log, ["start:a", "cancel:a:grp"])

    def test_cancel_by_instance(self):
        holder, log = _holder(), []
        act = Waiter(log, "a", Deferred())
        process.start_activity(holder, act)
        process.cancel_activity(holder, act, reason="inst")
        self.assertEqual(log, ["start:a", "cancel:a:inst"])

    def test_cancel_is_idempotent(self):
        holder, log = _holder(), []
        act = Waiter(log, "a", Deferred())
        process.start_activity(holder, act)
        process.cancel_activity(holder, "waiter", reason="first")
        process.cancel_activity(holder, "waiter", reason="second")  # no-op
        self.assertEqual(log, ["start:a", "cancel:a:first"])

    def test_cancelled_body_does_not_resume(self):
        holder, log = _holder(), []
        d = Deferred()
        act = Waiter(log, "a", d)
        process.start_activity(holder, act)
        act.cancel(reason="x")
        # firing the Deferred now must not push the body past its suspension
        self.assertNotIn("resume:a:None", log)


# --- exclusivity ------------------------------------------------------------
class TestActivityExclusivity(unittest.TestCase):
    def test_same_group_supersedes(self):
        holder, log = _holder(), []
        a1 = Waiter(log, "a1", Deferred())
        a2 = Waiter(log, "a2", Deferred())
        process.start_activity(holder, a1)
        process.start_activity(holder, a2)  # same exclusive_group → cancels a1
        self.assertIn("cancel:a1:superseded", log)
        self.assertIs(process.active_activity(holder, "waiter"), a2)
        self.assertEqual(len(process.get_activities(holder)), 1)

    def test_distinct_keys_and_groups_coexist(self):
        holder, log = _holder(), []

        class Other(Waiter):
            key = "other"
            exclusive_group = None

        process.start_activity(holder, Waiter(log, "w", Deferred()))
        process.start_activity(holder, Other(log, "o", Deferred()))
        self.assertEqual(len(process.get_activities(holder)), 2)


if __name__ == "__main__":
    unittest.main()
