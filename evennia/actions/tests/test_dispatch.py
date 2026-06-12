"""Tests for the cmdhandler bridge (CM1 Phase 4).

These exercise ``try_action_dispatch`` directly (with an isolated parser/engine
and fake actor), covering the routing decision, signal firing, disambiguation
install + replay, active-state capture, and profiling middleware, without
touching the real cmdhandler (the shim there is a thin call into this module).
"""

import unittest
from dataclasses import dataclass

from evennia.actions.action import Action, GameObject
from evennia.actions.actor import Actor
from evennia.actions.dispatch import (ProfilingMiddleware, clear_middlewares,
                                      register_middleware, try_action_dispatch)
from evennia.actions.engine import RuleEngine
from evennia.actions.exceptions import AmbiguousTarget
from evennia.actions.menus import DisambiguationState
from evennia.actions.parser import ActionParser, NoMatchAction
from evennia.actions.registry import ActionRegistry
from evennia.actions.result import CLAIM
from evennia.actions.rule import rule
from evennia.commands.signals import (on_command_error, on_command_post,
                                      on_command_pre)

ENGINE = RuleEngine()


def _sync(d):
    """Extract an already-fired Deferred's result, re-raising on failure."""
    out = {}
    d.addCallbacks(lambda r: out.__setitem__("result", r), lambda f: out.__setitem__("fail", f))
    if "fail" in out:
        out["fail"].raiseException()
    if "result" not in out:
        raise AssertionError("dispatch Deferred did not fire synchronously")
    return out["result"]


# --- test action + target ---------------------------------------------------
@dataclass
class Kick(Action):
    target: GameObject = None


@dataclass
class Zap(Action):
    """A verb whose only carry_out path is permission-gated."""


@dataclass
class Void(Action):
    """A registered verb no provider carries any rule for."""


@dataclass
class Poke(Action):
    """A verb whose parse always fails target resolution."""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        act = cls()
        act._unresolved = True
        return act


@dataclass
class Boom(Action):
    """A verb whose carry_out body raises."""


class RuleTarget:
    """A world object whose class carries a carry_out rule for ``Kick``."""

    def __init__(self, key):
        self.key = key
        self.kicked = []

    @rule(Kick, phase="carry_out")
    def on_kick(self, action, actor):
        self.kicked.append(action)
        return CLAIM


# --- fakes ------------------------------------------------------------------
class FakeNDB:
    pass


class FakeChar:
    """Effective object for the actor: holds states, sends msgs, and resolves
    searches via a configurable hook."""

    def __init__(self, key="Bob", location=None):
        self.key = key
        self.location = location
        self.account = None
        self.ndb = FakeNDB()
        self.messages = []
        self._search_hook = lambda name: None

    def msg(self, text=None, **kwargs):
        self.messages.append(text)

    def search(self, name):
        return self._search_hook(name)


def _make_registry():
    reg = ActionRegistry()
    Kick.__action_verbs__ = ("kick",)
    reg.register(Kick, ("kick",))
    return reg


def _make_full_registry():
    """Registry with the fail-closed fixture verbs alongside ``kick``."""
    reg = _make_registry()
    reg.register(Zap, ("zap",))
    reg.register(Void, ("void",))
    reg.register(Poke, ("poke",))
    reg.register(Boom, ("boom",))
    return reg


def _dispatch(actor, raw, parser):
    return _sync(
        try_action_dispatch(actor.effective, raw, actor=actor, engine=ENGINE, parser=parser)
    )


# --- routing ----------------------------------------------------------------
class TestRouting(unittest.TestCase):
    def setUp(self):
        self.parser = ActionParser(registry=_make_registry())
        self.char = FakeChar()
        self.actor = Actor(character=self.char)

    def test_known_verb_dispatches_through_engine(self):
        from evennia.actions.result import ActionTrace

        goblin = RuleTarget("goblin")
        self.char._search_hook = lambda name: goblin
        trace = _dispatch(self.actor, "kick goblin", self.parser)
        self.assertIsInstance(trace, ActionTrace)
        self.assertEqual(trace.outcome, "succeeded")
        self.assertEqual(len(goblin.kicked), 1)

    def test_unknown_verb_returns_handled_with_nomatch(self):
        handled = _dispatch(self.actor, "frobnicate the moon", self.parser)
        self.assertTrue(handled)
        self.assertIn("Huh?", self.char.messages[-1])

    def test_empty_line_handled_by_engine(self):
        handled = _dispatch(self.actor, "", self.parser)
        self.assertTrue(handled)


# --- fail-closed feedback (dispatch honesty) ---------------------------------
class FailClosedChar(FakeChar):
    """Effective object whose class carries the fail-closed fixture rules."""

    @rule(Zap, phase="carry_out", requires=lambda action, actor: False)
    def do_zap(self, action, actor):
        return CLAIM

    @rule(Poke, phase="carry_out")
    def do_poke(self, action, actor):
        from evennia.actions.result import SKIP

        if action._unresolved:
            return SKIP
        return CLAIM

    @rule(Boom, phase="carry_out")
    def do_boom(self, action, actor):
        raise RuntimeError("kapow")


class TestFailClosedFeedback(unittest.TestCase):
    """A parsed verb dispatch must never end in silence: a fully gated verb is
    indistinguishable from a nonexistent one (no-match feedback + security
    log), a verb with no responding rules produces a direct refusal, and
    legitimately-quiet outcomes (resolved carry_out, unresolved target,
    nomatch/noinput with their own providers) stay as-is."""

    def setUp(self):
        self.parser = ActionParser(registry=_make_full_registry())
        self.char = FailClosedChar()
        self.actor = Actor(character=self.char)

    def test_gated_verb_is_indistinguishable_from_unknown(self):
        from unittest import mock

        from evennia.utils import logger as ev_logger

        with mock.patch.object(ev_logger, "log_sec") as log_sec:
            trace = _dispatch(self.actor, "zap", self.parser)
        # Player-facing: exactly what an unknown verb produces, with no
        # suggestions (they would offer the hidden verb back), and no
        # permission message naming a real command.
        self.assertIn("Huh?", self.char.messages[-1])
        joined = "\n".join(str(m) for m in self.char.messages)
        self.assertNotIn("permission", joined.lower())
        self.assertNotIn("zap", joined.lower())
        # Staff-facing: the attempt lands in the security log.
        log_sec.assert_called_once()
        self.assertIn("Zap", log_sec.call_args.args[0])
        # The returned trace describes the typed verb, not the feedback.
        self.assertEqual(trace.carry_out_gated, 1)
        self.assertEqual(trace.carry_out_fired, 0)

    def test_gated_verb_masks_sensitive_input_in_seclog(self):
        # A password typed at the wrong prompt into a gated verb must not land
        # verbatim in the security log: the raw input is masked the same way
        # the legacy command path masks it.
        from unittest import mock

        from evennia.utils import logger as ev_logger

        reg = _make_full_registry()
        reg.register(Zap, ("password",))
        parser = ActionParser(registry=reg)
        with mock.patch.object(ev_logger, "log_sec") as log_sec:
            _dispatch(self.actor, "password hunter2secret", parser)
        log_sec.assert_called_once()
        logged = log_sec.call_args.args[0]
        self.assertNotIn("hunter2secret", logged)
        self.assertIn("*", logged)

    def test_verb_with_no_rules_sends_default_feedback(self):
        trace = _dispatch(self.actor, "void", self.parser)
        self.assertIn("You can't do that.", self.char.messages)
        self.assertEqual(trace.outcome, "no_rules")

    def test_successful_carry_out_adds_no_feedback(self):
        goblin = RuleTarget("goblin")
        self.char._search_hook = lambda name: goblin
        trace = _dispatch(self.actor, "kick goblin", self.parser)
        self.assertEqual(self.char.messages, [])
        self.assertEqual(trace.carry_out_fired, 1)

    def test_unresolved_action_stays_quiet(self):
        # parse() set _unresolved (search already reported the miss in the real
        # flow); the fail-closed fallback must not pile on.
        _dispatch(self.actor, "poke ghost", self.parser)
        self.assertEqual(self.char.messages, [])

    def test_nomatch_keeps_its_own_feedback(self):
        _dispatch(self.actor, "frobnicate", self.parser)
        self.assertIn("Huh?", self.char.messages[-1])
        self.assertNotIn("You can't do that.", self.char.messages)

    def test_empty_line_stays_silent(self):
        _dispatch(self.actor, "", self.parser)
        self.assertEqual(self.char.messages, [])


class TestRuleExceptionFeedback(unittest.TestCase):
    """A carry_out body that raises must reach the player, not just the log."""

    def setUp(self):
        self.parser = ActionParser(registry=_make_full_registry())
        self.char = FailClosedChar()
        self.actor = Actor(character=self.char)

    def test_carry_out_exception_messages_actor(self):
        trace = _dispatch(self.actor, "boom", self.parser)
        joined = "\n".join(str(m) for m in self.char.messages)
        self.assertIn("untrapped error", joined.lower())
        # The errored rule fired (recorded as FAIL), so the fail-closed
        # fallback must not also fire.
        self.assertNotIn("You can't do that.", self.char.messages)
        self.assertEqual(trace.carry_out_fired, 1)


# --- signals ----------------------------------------------------------------
class TestSignals(unittest.TestCase):
    def setUp(self):
        self.parser = ActionParser(registry=_make_registry())
        self.char = FakeChar()
        self.actor = Actor(character=self.char)
        self.pre, self.post, self.error = [], [], []
        on_command_pre.connect(self._on_pre, weak=False)
        on_command_post.connect(self._on_post, weak=False)
        on_command_error.connect(self._on_error, weak=False)

    def tearDown(self):
        on_command_pre.disconnect(self._on_pre)
        on_command_post.disconnect(self._on_post)
        on_command_error.disconnect(self._on_error)

    def _on_pre(self, sender, **kwargs):
        self.pre.append((sender, kwargs))

    def _on_post(self, sender, **kwargs):
        self.post.append((sender, kwargs))

    def _on_error(self, sender, **kwargs):
        self.error.append((sender, kwargs))

    def test_engine_dispatch_fires_pre_and_post(self):
        goblin = RuleTarget("goblin")
        self.char._search_hook = lambda name: goblin
        _dispatch(self.actor, "kick goblin", self.parser)
        self.assertEqual([s for s, _ in self.pre], [Kick])
        self.assertEqual([s for s, _ in self.post], [Kick])
        self.assertIn("elapsed_ms", self.post[0][1])

    def test_empty_line_fires_noinput_signals(self):
        from evennia.actions.parser import NoInputAction

        _dispatch(self.actor, "", self.parser)
        self.assertEqual([s for s, _ in self.pre], [NoInputAction])
        self.assertEqual([s for s, _ in self.post], [NoInputAction])

    def test_unknown_verb_fires_nomatch_signals(self):
        _dispatch(self.actor, "frobnicate", self.parser)
        self.assertEqual([s for s, _ in self.pre], [NoMatchAction])
        self.assertEqual([s for s, _ in self.post], [NoMatchAction])

    def test_dispatch_failure_fires_error_signal_not_post(self):
        # When the engine dispatch itself raises (an engine-level failure, not a
        # buggy rule, which the engine records as FAIL), the wrapper fires
        # on_command_pre then on_command_error (with the exception + traceback)
        # and re-raises; NO post fires.
        from unittest import mock

        goblin = RuleTarget("goblin")
        self.char._search_hook = lambda name: goblin
        with mock.patch.object(ENGINE, "dispatch", side_effect=RuntimeError("kaboom")):
            with self.assertRaises(RuntimeError):
                _dispatch(self.actor, "kick goblin", self.parser)
        self.assertEqual([s for s, _ in self.pre], [Kick])
        self.assertEqual([s for s, _ in self.post], [])
        self.assertEqual([s for s, _ in self.error], [Kick])
        err_kwargs = self.error[0][1]
        self.assertIsInstance(err_kwargs["exc"], RuntimeError)
        self.assertEqual(str(err_kwargs["exc"]), "kaboom")
        self.assertIn("RuntimeError", err_kwargs["traceback_text"])
        self.assertIn("kaboom", err_kwargs["traceback_text"])

    def test_signals_carry_session_and_shared_trace_id(self):
        # The session passed to dispatch surfaces verbatim in signal kwargs, and
        # pre/post share the single trace_id read at the top of the dispatch.
        from evennia.utils.command_trace import (begin_command_trace,
                                                 end_command_trace)

        goblin = RuleTarget("goblin")
        self.char._search_hook = lambda name: goblin
        sentinel_session = object()
        tid = begin_command_trace(raw_string="kick goblin", cmd_key="kick")
        try:
            _sync(
                try_action_dispatch(
                    self.actor.effective,
                    "kick goblin",
                    actor=self.actor,
                    engine=ENGINE,
                    parser=self.parser,
                    session=sentinel_session,
                )
            )
        finally:
            end_command_trace()
        self.assertIs(self.pre[0][1]["session"], sentinel_session)
        self.assertIs(self.post[0][1]["session"], sentinel_session)
        self.assertEqual(self.pre[0][1]["trace_id"], tid)
        self.assertEqual(self.post[0][1]["trace_id"], tid)


# --- disambiguation ---------------------------------------------------------
class TestDisambiguation(unittest.TestCase):
    def setUp(self):
        self.parser = ActionParser(registry=_make_registry())
        self.char = FakeChar()
        self.actor = Actor(character=self.char)

    def test_ambiguous_target_installs_state_and_prompts(self):
        candidates = [RuleTarget("goblin"), RuleTarget("goblin chief")]

        def _ambiguous(name):
            raise AmbiguousTarget(candidates=candidates, original_raw=name)

        self.char._search_hook = _ambiguous
        # The bridge consumed the line (prompt sent) without an engine
        # dispatch, so there is no trace.
        handled = _dispatch(self.actor, "kick goblin", self.parser)
        self.assertIsNone(handled)
        self.assertTrue(self.actor.has_state(DisambiguationState))
        prompt = "\n".join(self.char.messages)
        self.assertIn("Which one did you mean?", prompt)
        state = self.actor.state_objects[-1]
        self.assertEqual(state.pending_raw, "kick goblin")
        self.assertEqual(state.ambiguous_name, "goblin")

    def test_valid_choice_replays_with_override(self):
        c1, c2 = RuleTarget("goblin"), RuleTarget("goblin chief")
        # The replayed search must hand back the chosen candidate.
        self.char._search_hook = lambda name: self.actor._take_search_override(name)
        self.actor.enter_state(
            DisambiguationState([c1, c2], pending_raw="kick goblin", ambiguous_name="goblin")
        )
        # The replayed dispatch's trace comes back through the resolution.
        trace = _dispatch(self.actor, "2", self.parser)
        self.assertEqual(trace.outcome, "succeeded")
        self.assertFalse(self.actor.has_state(DisambiguationState))
        self.assertEqual(len(c2.kicked), 1)
        self.assertEqual(len(c1.kicked), 0)

    def test_invalid_choice_cancels(self):
        c1, c2 = RuleTarget("goblin"), RuleTarget("goblin chief")
        self.actor.enter_state(
            DisambiguationState([c1, c2], pending_raw="kick goblin", ambiguous_name="goblin")
        )
        # Cancelled choice: the bridge consumed the line, no engine dispatch.
        handled = _dispatch(self.actor, "99", self.parser)
        self.assertIsNone(handled)
        self.assertFalse(self.actor.has_state(DisambiguationState))
        self.assertIn("Invalid choice. Cancelled.", self.char.messages)
        self.assertEqual(len(c1.kicked) + len(c2.kicked), 0)


# --- profiling middleware ---------------------------------------------------
class TestProfiling(unittest.TestCase):
    def setUp(self):
        self.parser = ActionParser(registry=_make_registry())
        self.char = FakeChar()
        self.actor = Actor(character=self.char)
        self.prof = ProfilingMiddleware()
        register_middleware(self.prof)

    def tearDown(self):
        clear_middlewares()

    def test_engine_path_records_timing(self):
        goblin = RuleTarget("goblin")
        self.char._search_hook = lambda name: goblin
        _dispatch(self.actor, "kick goblin", self.parser)
        self.assertEqual(len(self.prof.timings[Kick]), 1)

    def test_legacy_path_records_via_signal(self):
        # The legacy cmdset path records through the on_command_post receiver.
        self.prof.on_command_post(sender=Kick, elapsed_ms=12.5)
        self.assertEqual(self.prof.timings[Kick], [12.5])


# --- Actor.from_caller (callertype disambiguation) --------------------------
class FakeSession:
    """A ServerSession stand-in: carries BOTH .account and a puppet (exposed
    via ``get_puppet()`` like the real session), which is exactly why
    attribute-sniffing misclassifies it without a callertype."""

    def __init__(self, account=None, puppet=None, binding=None):
        self.account = account
        self._puppet = puppet
        self.binding = binding

    def get_puppet(self):
        return self._puppet


class TestFromCaller(unittest.TestCase):
    def test_session_callertype_uses_puppet_as_character(self):
        char = FakeChar()
        acct = FakeNDB()
        sess = FakeSession(account=acct, puppet=char)
        actor = Actor.from_caller(sess, session=sess, callertype="session")
        self.assertIs(actor.character, char)
        self.assertIs(actor.effective, char)
        self.assertIs(actor.account, acct)
        self.assertIs(actor.session, sess)

    def test_session_callertype_ooc_has_no_character(self):
        acct = FakeNDB()
        sess = FakeSession(account=acct, puppet=None)
        actor = Actor.from_caller(sess, session=sess, callertype="session")
        self.assertIsNone(actor.character)
        self.assertIs(actor.effective, acct)

    def test_object_callertype_treats_caller_as_character(self):
        char = FakeChar()
        char.account = FakeNDB()
        actor = Actor.from_caller(char, callertype="object")
        self.assertIs(actor.character, char)
        self.assertIs(actor.account, char.account)


if __name__ == "__main__":
    unittest.main()
