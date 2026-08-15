"""Tests for the RuleEngine four-phase dispatch core (CM1 Phase 1, incl. 1g).

``dispatch`` / ``explain`` return ``Deferred[ActionTrace]``. For all-synchronous
dispatches the Deferred is already fired, so :func:`_sync` extracts the result
(re-raising on failure). The 1g suspension tests drive the async paths by either
auto-answering ``get_input`` / ``_sleep`` (so dispatch still completes inline) or
by holding a rule's ``Deferred`` unfired to assert phase serialization.
"""

import asyncio
import sys
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest import mock

from twisted.internet.defer import Deferred, succeed

from evennia.actions.action import Action
from evennia.actions.engine import RuleEngine
from evennia.actions.menus import MenuPrompt, format_menu_prompt
from evennia.actions.rule import rule

# The package re-exports the ``engine`` *instance*, which shadows the ``engine``
# submodule attribute on the package — so ``import evennia.actions.engine`` would
# bind the instance, not the module. Grab the module object from sys.modules.
engine_mod = sys.modules["evennia.actions.engine"]
from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext
from evennia.actions.exceptions import ActionError
from evennia.actions.predicate import HasCapability, HasTag
from evennia.actions.result import CLAIM, FAIL, PASS, REDIRECT
from evennia.utils import logger


# --- test action types ------------------------------------------------------
@dataclass
class Kick(Action):
    target: object = None


@dataclass
class Look(Action):
    target: object = None


# --- helpers ----------------------------------------------------------------
class _Perms:
    def __init__(self, perms):
        self._perms = list(perms)

    def all(self):
        return list(self._perms)


def _actor(perms=(), tags_has=None):
    """Build a stand-in actor with a capturing ``msg`` and an effective object."""
    msgs = []
    eff = SimpleNamespace(
        key="Bob",
        permissions=_Perms(perms),
        account=None,
        ndb=SimpleNamespace(),
    )
    eff.has_capability = lambda key, **kwargs: key in set(perms)
    if tags_has is not None:
        eff.tags = SimpleNamespace(has=tags_has)
    eff.msg = lambda text, **kw: msgs.append(text)
    actor = Actor(session=None, account=None, character=eff)
    actor.messages = msgs
    return actor


def _ctx(*providers):
    return ActionContext(providers=list(providers))


def _sync(d):
    """Extract an already-fired Deferred/coroutine's result, re-raising on failure."""
    import inspect

    from twisted.internet.defer import ensureDeferred

    if inspect.iscoroutine(d):
        # dispatch/engine are now `async def`; run the coroutine to a Deferred.
        d = ensureDeferred(d)
    out = {}
    d.addCallbacks(lambda r: out.__setitem__("result", r), lambda f: out.__setitem__("fail", f))
    if "fail" in out:
        out["fail"].raiseException()
    if "result" not in out:
        raise AssertionError("dispatch did not complete synchronously")
    return out["result"]


ENGINE = RuleEngine()


def _dispatch(*args, **kwargs):
    """``dispatch`` is ``async`` now; wrap the coroutine as a Deferred for tests
    that drive a suspension manually (``.called`` + firing a rule's Deferred)."""
    from twisted.internet.defer import ensureDeferred

    return ensureDeferred(ENGINE.dispatch(*args, **kwargs))


# --- provider classes -------------------------------------------------------
class CheckGate:
    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="check", priority=10)
    def deny(self, action, actor):
        self.fired.append("deny")
        return FAIL("no kicking")

    @rule(Kick, phase="check", priority=1)
    def after_deny(self, action, actor):
        self.fired.append("after_deny")
        return PASS


class Worker:
    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="carry_out", priority=10)
    def claim_it(self, action, actor):
        self.fired.append("claim_it")
        return CLAIM

    @rule(Kick, phase="carry_out", priority=1)
    def after_claim(self, action, actor):
        self.fired.append("after_claim")
        return PASS


class Faulty:
    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="carry_out", priority=10)
    def boom(self, action, actor):
        self.fired.append("boom")
        raise RuntimeError("kaboom")

    @rule(Kick, phase="carry_out", priority=1)
    def still_runs(self, action, actor):
        self.fired.append("still_runs")
        return PASS


class Redirector:
    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="before")
    def to_look(self, action, actor):
        self.fired.append("to_look")
        return REDIRECT(Look())

    @rule(Look, phase="carry_out")
    def looked(self, action, actor):
        self.fired.append("looked")
        return PASS


class InfiniteRedirect:
    @rule(Kick, phase="before")
    def loop(self, action, actor):
        return REDIRECT(Kick())


class FlatlinedState:
    @rule(Action, phase="check", priority=999)
    def block_all(self, action, actor):
        return FAIL("you are flatlined")


class StaffOnly:
    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="carry_out", requires=HasCapability("engine.world.build"))
    def staff_work(self, action, actor):
        self.fired.append("staff_work")
        return PASS


class TwoTagRules:
    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="check", priority=2, requires=HasTag("vip"))
    def rule_a(self, action, actor):
        self.fired.append("a")
        return PASS

    @rule(Kick, phase="check", priority=1, requires=HasTag("vip"))
    def rule_b(self, action, actor):
        self.fired.append("b")
        return PASS


class AllPhases:
    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="before")
    def b(self, action, actor):
        self.fired.append("before")
        return PASS

    @rule(Kick, phase="check")
    def c(self, action, actor):
        self.fired.append("check")
        return PASS

    @rule(Kick, phase="carry_out")
    def co(self, action, actor):
        self.fired.append("carry_out")
        return PASS

    @rule(Kick, phase="report")
    def r(self, action, actor):
        self.fired.append("report")
        return PASS


class BadCheckClaim:
    @rule(Kick, phase="check")
    def claims(self, action, actor):
        return CLAIM


class BadCheckGenerator:
    @rule(Kick, phase="check")
    def gen(self, action, actor):
        yield "a prompt"  # generator body — illegal in the synchronous check phase


# --- 1g provider classes ----------------------------------------------------
class Interactive:
    """A generator (`@interactive`-shaped) carry_out rule that asks the player."""

    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="carry_out", priority=10)
    def confirm(self, action, actor):
        self.fired.append("ask")
        answer = yield "Proceed?"
        self.fired.append(f"got:{answer}")
        if answer == "yes":
            return CLAIM
        return PASS

    @rule(Kick, phase="carry_out", priority=1)
    def after_confirm(self, action, actor):
        self.fired.append("after_confirm")
        return PASS


class Waiter:
    """A generator carry_out rule that pauses (numeric yield)."""

    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="carry_out")
    def wait(self, action, actor):
        self.fired.append("before_wait")
        yield 5
        self.fired.append("after_wait")
        return PASS


class Deferring:
    """A carry_out rule that returns a Deferred (e.g. defer.in_thread)."""

    def __init__(self, fired, d):
        self.fired = fired
        self.d = d

    @rule(Kick, phase="carry_out", priority=10)
    def work(self, action, actor):
        self.fired.append("work")
        return self.d

    @rule(Kick, phase="carry_out", priority=1)
    def after_work(self, action, actor):
        self.fired.append("after_work")
        return PASS


class DeferringReport:
    """A report rule that returns a Deferred; report never short-circuits."""

    def __init__(self, fired, d):
        self.fired = fired
        self.d = d

    @rule(Kick, phase="report", priority=10)
    def slow_report(self, action, actor):
        self.fired.append("slow_report")
        return self.d

    @rule(Kick, phase="report", priority=1)
    def fast_report(self, action, actor):
        self.fired.append("fast_report")
        return PASS


# --- synchronous tests ------------------------------------------------------
class TestCheckPhase(unittest.TestCase):
    def test_check_short_circuits_on_first_fail(self):
        fired = []
        trace = _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(CheckGate(fired))))
        self.assertEqual(fired, ["deny"])  # after_deny never fired
        self.assertEqual(trace.outcome, "blocked")
        self.assertEqual(trace.block_message, "no kicking")

    def test_check_block_message_sent_to_actor(self):
        actor = _actor()
        _sync(ENGINE.dispatch(Kick(), actor, _ctx(CheckGate([]))))
        self.assertEqual(actor.messages, ["no kicking"])

    def test_check_claim_is_contract_violation_and_warns(self):
        with mock.patch.object(logger, "log_warn") as warn:
            trace = _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(BadCheckClaim())))
        self.assertEqual(warn.call_count, 1)
        self.assertNotEqual(trace.outcome, "blocked")

    def test_check_generator_return_raises(self):
        with self.assertRaises(ActionError):
            _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(BadCheckGenerator())))


class TestCarryOutPhase(unittest.TestCase):
    def test_claim_stops_further_carry_out(self):
        fired = []
        _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(Worker(fired))))
        self.assertEqual(fired, ["claim_it"])  # after_claim never fired

    def test_exception_does_not_stop_other_rules(self):
        fired = []
        with mock.patch.object(logger, "log_trace"):
            trace = _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(Faulty(fired))))
        self.assertEqual(fired, ["boom", "still_runs"])
        self.assertEqual(trace.outcome, "succeeded")


class TestBeforePhaseRedirect(unittest.TestCase):
    def test_redirect_restarts_dispatch(self):
        fired = []
        trace = _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(Redirector(fired))))
        self.assertEqual(fired, ["to_look", "looked"])
        self.assertEqual(trace.redirect_count, 1)
        self.assertEqual(trace.outcome, "succeeded")

    def test_redirect_loop_guard(self):
        with self.assertRaises(ActionError):
            _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(InfiniteRedirect())))


class TestCatchAllStateGate(unittest.TestCase):
    def test_fires_for_unrelated_action_type(self):
        trace = _sync(ENGINE.dispatch(Look(), _actor(), _ctx(FlatlinedState())))
        self.assertEqual(trace.outcome, "blocked")
        self.assertEqual(trace.block_message, "you are flatlined")

    def test_fires_for_named_action_type_too(self):
        trace = _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(FlatlinedState())))
        self.assertEqual(trace.outcome, "blocked")


class TestRequiresGate(unittest.TestCase):
    def test_requires_false_skips_body(self):
        fired = []
        trace = _sync(ENGINE.dispatch(Kick(), _actor(perms=[]), _ctx(StaffOnly(fired))))
        self.assertEqual(fired, [])  # body never ran
        skips = [pt for pt in trace.phases if pt.result.is_skip]
        self.assertEqual(len(skips), 1)

    def test_requires_true_runs_body(self):
        fired = []
        _sync(ENGINE.dispatch(Kick(), _actor(perms=["engine.world.build"]), _ctx(StaffOnly(fired))))
        self.assertEqual(fired, ["staff_work"])

    def test_capability_predicate_uses_structured_facade(self):
        actor = _actor(perms=["engine.world.build"])
        actor.effective.has_capability = mock.Mock(return_value=True)
        _sync(ENGINE.dispatch(Kick(), actor, _ctx(StaffOnly([]))))
        actor.effective.has_capability.assert_called_once_with("engine.world.build")


class TestPerDispatchMemo(unittest.TestCase):
    def test_shared_db_leaf_evaluated_once(self):
        has = mock.Mock(return_value=True)
        actor = _actor(tags_has=has)
        fired = []
        _sync(ENGINE.dispatch(Kick(), actor, _ctx(TwoTagRules(fired))))
        self.assertEqual(fired, ["a", "b"])  # both passed
        self.assertEqual(has.call_count, 1)  # HasTag("vip") query ran once


class TestDryRunAndExplain(unittest.TestCase):
    def test_dry_run_skips_bodies_but_evaluates_requires(self):
        fired = []
        trace = _sync(
            ENGINE.dispatch(
                Kick(),
                _actor(perms=["engine.world.build"]),
                _ctx(StaffOnly(fired)),
                dry_run=True,
            )
        )
        self.assertEqual(fired, [])  # body skipped
        self.assertTrue(any(pt.result.is_pass for pt in trace.phases))

    def test_dry_run_requires_false_still_skips(self):
        trace = _sync(ENGINE.dispatch(Kick(), _actor(perms=[]), _ctx(StaffOnly([])), dry_run=True))
        self.assertTrue(any(pt.result.is_skip for pt in trace.phases))

    def test_explain_returns_full_trace_all_phases(self):
        trace = _sync(ENGINE.explain(Kick(), _actor(), _ctx(AllPhases([]))))
        for phase in ("before", "check", "carry_out", "report"):
            self.assertEqual(len(trace.for_phase(phase)), 1, phase)

    def test_explain_does_not_fire_carry_out_or_report(self):
        fired = []
        _sync(ENGINE.explain(Kick(), _actor(), _ctx(AllPhases(fired))))
        self.assertEqual(fired, ["check"])

    def test_explain_evaluates_check_but_suppresses_message_leak(self):
        # A check body that violates purity by messaging must not leak output
        # during a read-only explain; the predicate still evaluates.
        class LeakyCheck:
            def __init__(self, fired):
                self.fired = fired

            @rule(Kick, phase="check")
            def leak(self, action, actor):
                self.fired.append("check")
                actor.msg("leaked!")
                return FAIL("blocked")

        fired = []
        actor = _actor()
        with mock.patch.object(logger, "log_warn") as warn:
            trace = _sync(ENGINE.explain(Kick(), actor, _ctx(LeakyCheck(fired))))
        self.assertEqual(fired, ["check"])  # predicate still evaluated
        self.assertEqual(actor.messages, [])  # message suppressed under explain
        self.assertEqual(trace.outcome, "blocked")  # explain reports the block
        warn.assert_called_once()

    def test_real_dispatch_check_message_not_suppressed(self):
        # Outside dry_run the guard is not active: a (non-dry) block sends its
        # message normally, confirming the guard is explain-only.
        class BlockingCheck:
            @rule(Kick, phase="check")
            def deny(self, action, actor):
                return FAIL("blocked!")

        actor = _actor()
        _sync(ENGINE.dispatch(Kick(), actor, _ctx(BlockingCheck())))
        self.assertIn("blocked!", actor.messages)


class TestOutcomeAndProviderOrder(unittest.TestCase):
    def test_no_rules_outcome(self):
        class Empty:
            pass

        trace = _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(Empty())))
        self.assertEqual(trace.outcome, "no_rules")

    def test_dispatch_returns_already_fired_deferred_when_no_suspension(self):
        d = _dispatch(Kick(), _actor(), _ctx(Worker([])))
        self.assertIsInstance(d, Deferred)
        self.assertTrue(d.called)


# --- 1g: deferred / interactive driving -------------------------------------
class TestInteractiveRule(unittest.TestCase):
    def _auto_answer(self, answer):
        def fake_deferred(actor, prompt):
            d = Deferred()
            if prompt:
                RuleEngine._caller(actor).msg(prompt)
            d.callback(answer)
            return d

        return mock.patch.object(engine_mod, "_get_input_future", side_effect=fake_deferred)

    def test_interactive_resumes_with_input_and_claim_stops_phase(self):
        fired = []
        with self._auto_answer("yes"):
            trace = _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(Interactive(fired))))
        # confirm asked, got "yes", returned CLAIM → after_confirm never fired
        self.assertEqual(fired, ["ask", "got:yes"])
        self.assertEqual(trace.outcome, "succeeded")

    def test_interactive_no_claim_lets_phase_continue(self):
        fired = []
        with self._auto_answer("no"):
            _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(Interactive(fired))))
        self.assertEqual(fired, ["ask", "got:no", "after_confirm"])

    def test_numeric_yield_pauses_via_sleep(self):
        fired = []
        with mock.patch.object(engine_mod, "_sleep", return_value=succeed(None)) as slept:
            _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(Waiter(fired))))
        self.assertEqual(fired, ["before_wait", "after_wait"])
        slept.assert_called_once_with(5)


class MenuPicker:
    """Generator carry_out that uses MenuPrompt."""

    def __init__(self, fired):
        self.fired = fired

    @rule(Kick, phase="carry_out", priority=10)
    def pick(self, action, actor):
        choice = yield MenuPrompt(
            "Pick one:",
            options=[("a", "Alpha"), ("b", "Beta")],
        )
        self.fired.append(f"choice:{choice}")
        return CLAIM


class TestMenuPromptRule(unittest.TestCase):
    def _auto_answer(self, answer):
        def fake_deferred(actor, prompt):
            d = Deferred()
            if prompt:
                RuleEngine._caller(actor).msg(prompt)
            d.callback(answer)
            return d

        return mock.patch.object(engine_mod, "_get_input_future", side_effect=fake_deferred)

    def test_menu_prompt_resumes_with_numeric_choice(self):
        fired = []
        with self._auto_answer("1"):
            _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(MenuPicker(fired))))
        self.assertEqual(fired, ["choice:a"])

    def test_menu_prompt_quit_returns_none(self):
        fired = []
        with self._auto_answer("q"):
            _sync(ENGINE.dispatch(Kick(), _actor(), _ctx(MenuPicker(fired))))
        self.assertEqual(fired, ["choice:None"])

    def test_twisted_driver_waits_on_pending_asyncio_input_future(self):
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        fired = []
        try:
            with mock.patch.object(engine_mod, "_get_input_future", return_value=future):
                dispatch = _dispatch(Kick(), _actor(), _ctx(MenuPicker(fired)))
            self.assertFalse(dispatch.called)
            loop.call_soon(future.set_result, "1")
            loop.run_until_complete(dispatch.asFuture(loop))
        finally:
            loop.close()
        self.assertEqual(fired, ["choice:a"])


class TestFormatMenuPrompt(unittest.TestCase):
    def test_shows_option_keys_not_renumbered(self):
        text = format_menu_prompt(
            MenuPrompt(
                "Header",
                options=[("c", "Create a group"), ("b", "Back")],
                allow_quit=True,
            )
        )
        self.assertIn("|wc|n: Create a group", text)
        self.assertIn("|wb|n: Back", text)
        self.assertNotIn("1: Create", text)

    def test_skips_global_quit_when_exit_option_present(self):
        text = format_menu_prompt(
            MenuPrompt(
                "Hub",
                options=[("q", "Exit interface")],
                allow_quit=True,
            )
        )
        self.assertIn("|wq|n: Exit interface", text)
        self.assertNotIn("|wq|n: Quit", text)


class TestDeferredRule(unittest.TestCase):
    def test_native_asyncio_dispatch_waits_on_pending_deferred_rule(self):
        loop = asyncio.new_event_loop()
        deferred = Deferred()
        fired = []

        async def drive():
            dispatch = asyncio.create_task(
                ENGINE.dispatch(Kick(), _actor(), _ctx(Deferring(fired, deferred)))
            )
            await asyncio.sleep(0)
            self.assertFalse(dispatch.done())
            self.assertEqual(fired, ["work"])
            deferred.callback(PASS)
            return await dispatch

        try:
            trace = loop.run_until_complete(drive())
        finally:
            loop.close()
        self.assertEqual(fired, ["work", "after_work"])
        self.assertEqual(trace.outcome, "succeeded")

    def test_phase_serializes_until_deferred_fires(self):
        d = Deferred()
        fired = []
        dispatch_d = _dispatch(Kick(), _actor(), _ctx(Deferring(fired, d)))
        # carry_out suspended on the unfired Deferred — phase has not advanced
        self.assertFalse(dispatch_d.called)
        self.assertEqual(fired, ["work"])
        # resolve with a non-CLAIM result → phase advances to the next rule
        d.callback(PASS)
        self.assertEqual(fired, ["work", "after_work"])
        trace = _sync(dispatch_d)
        self.assertEqual(trace.outcome, "succeeded")

    def test_deferred_claim_stops_phase(self):
        d = Deferred()
        fired = []
        dispatch_d = _dispatch(Kick(), _actor(), _ctx(Deferring(fired, d)))
        d.callback(CLAIM)
        self.assertEqual(fired, ["work"])  # after_work never fired
        _sync(dispatch_d)

    def test_report_serializes_and_never_short_circuits(self):
        d = Deferred()
        fired = []
        dispatch_d = _dispatch(Kick(), _actor(), _ctx(DeferringReport(fired, d)))
        self.assertFalse(dispatch_d.called)
        self.assertEqual(fired, ["slow_report"])  # next report rule waits
        d.callback(CLAIM)  # CLAIM is ignored in report
        self.assertEqual(fired, ["slow_report", "fast_report"])
        _sync(dispatch_d)


if __name__ == "__main__":
    unittest.main()
