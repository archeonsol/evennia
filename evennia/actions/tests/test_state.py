"""Tests for Actor, ActionContextBuilder, StateProvider, and the interaction
states EvMenuState / DisambiguationState (CM1 Phase 3).

State and dispatch behaviors are exercised through the real ``RuleEngine`` (its
``dispatch`` returns a ``Deferred`` that fires synchronously here), so these
tests cover the state machinery *and* its integration with the engine's
before/check/carry_out phases and the REDIRECT loop.
"""

import unittest
from dataclasses import dataclass

from evennia.actions.action import Action
from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext, ActionContextBuilder
from evennia.actions.engine import RuleEngine
from evennia.actions.exceptions import AmbiguousTarget
from evennia.actions.menus import (
    DisambiguationState,
    EvMenuState,
    MenuInputAction,
)
from evennia.actions.parser import ActionParser
from evennia.actions.registry import ActionRegistry
from evennia.actions.result import FAIL, SKIP
from evennia.actions.rule import rule
from evennia.actions.state import (
    StateProvider,
    enter_state,
    exit_state,
    get_states,
    has_state,
)
from evennia.actions.action import GameObject


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


# --- fakes ------------------------------------------------------------------
class FakeNDB:
    """A bare attribute bag standing in for Evennia's non-persistent handler."""


class FakeObj:
    def __init__(self, key):
        self.key = key

    def __repr__(self):
        return f"<FakeObj {self.key}>"


class FakeChar:
    def __init__(self, key="Bob", location=None):
        self.key = key
        self.location = location
        self.account = None
        self.ndb = FakeNDB()
        self.messages = []

    def msg(self, text=None, **kwargs):
        self.messages.append(text)

    def search(self, name):
        return None


# --- test action types ------------------------------------------------------
@dataclass
class Look(Action):
    pass


@dataclass
class Move(Action):
    pass


@dataclass
class Attack(Action):
    target: GameObject = None


@dataclass
class Choice(Action):
    """Stand-in for "whatever the player typed next" during an interaction."""


# --- test state -------------------------------------------------------------
class TestFlatlined(StateProvider):
    @rule(Action, phase="check", priority=1000)
    def block_all(self, action, actor):
        if isinstance(action, Look):
            return SKIP
        return FAIL("You are flatlined.")


# --- Actor ------------------------------------------------------------------
class TestActor(unittest.TestCase):
    def test_effective_prefers_character(self):
        char = FakeChar()
        acct = FakeObj("acct")
        actor = Actor(character=char, account=acct)
        self.assertIs(actor.effective, char)

    def test_effective_falls_back_to_account(self):
        acct = FakeObj("acct")
        actor = Actor(character=None, account=acct)
        self.assertIs(actor.effective, acct)

    def test_location_from_character(self):
        room = FakeObj("room")
        actor = Actor(character=FakeChar(location=room))
        self.assertIs(actor.location, room)

    def test_location_none_when_ooc(self):
        actor = Actor(character=None, account=FakeObj("acct"))
        self.assertIsNone(actor.location)


# --- state functions --------------------------------------------------------
class TestStateLifecycle(unittest.TestCase):
    def test_enter_has_exit_roundtrip(self):
        char = FakeChar()
        state = TestFlatlined()
        self.assertFalse(has_state(char, TestFlatlined))
        enter_state(char, state)
        self.assertTrue(has_state(char, TestFlatlined))
        self.assertEqual(get_states(char), [state])
        removed = exit_state(char, TestFlatlined)
        self.assertEqual(removed, [state])
        self.assertFalse(has_state(char, TestFlatlined))

    def test_actor_delegates_state_methods(self):
        char = FakeChar()
        actor = Actor(character=char)
        state = TestFlatlined()
        actor.enter_state(state)
        self.assertTrue(actor.has_state(TestFlatlined))
        self.assertEqual(actor.state_objects, [state])
        actor.exit_state(TestFlatlined)
        self.assertFalse(actor.has_state(TestFlatlined))

    def test_exit_on_empty_is_noop(self):
        char = FakeChar()
        self.assertEqual(exit_state(char, TestFlatlined), [])


# --- ActionContextBuilder ---------------------------------------------------
class TestContextBuilder(unittest.TestCase):
    def test_provider_order_matches_spec(self):
        room = FakeObj("room")
        char = FakeChar(location=room)
        item = FakeObj("sword")
        other = FakeObj("npc")
        target = FakeObj("ball")
        room.contents = [char, other, target]
        char.ndb.equipped_for_rules = [item]
        state = TestFlatlined()
        char.ndb.active_states = [state]

        actor = Actor(character=char)
        ctx = ActionContextBuilder().build(actor, "kick ball", targets=[target])

        # states → effective → equipped → location → targets → other contents,
        # with char + target de-duped out of room.contents.
        self.assertEqual(ctx.providers, [state, char, item, room, target, other])
        self.assertEqual(ctx.raw_string, "kick ball")
        self.assertIs(ctx.actor, actor)

    def test_no_location_tolerated(self):
        actor = Actor(character=None, account=FakeObj("acct"))
        ctx = ActionContextBuilder().build(actor, "")
        self.assertEqual(ctx.providers, [actor.effective])

    def test_account_callertype_inserts_account_provider(self):
        acct = FakeObj("acct")
        char = FakeChar(key="Hero")
        actor = Actor(account=acct, character=char)
        ctx = ActionContextBuilder().build(actor, "@audit", callertype="account")
        self.assertEqual(ctx.providers[0], acct)
        self.assertEqual(ctx.providers[1], char)

    def test_session_callertype_inserts_account_for_primary_handler(self):
        # A real primary-handler account and a real character each carry a rule
        # for the dispatched action, so the B2 prefilter keeps them. (A rule-less
        # provider is *correctly* dropped — that is the prefilter's whole point;
        # see context.py build() docstring.)
        @dataclass
        class AccountPrimary(Action):
            pass

        class FakeAccountType:
            @rule(AccountPrimary, phase="check")
            def _r(self, action, actor):
                return SKIP

        AccountPrimary.__primary_handler__ = FakeAccountType

        class RuledChar(FakeChar):
            @rule(AccountPrimary, phase="check")
            def _r(self, action, actor):
                return SKIP

        acct = FakeAccountType()
        char = RuledChar(key="Hero")
        actor = Actor(account=acct, character=char)
        ctx = ActionContextBuilder().build(
            actor, "@who", callertype="session", action_type=AccountPrimary
        )
        self.assertEqual(ctx.providers[0], acct)
        self.assertEqual(ctx.providers[1], char)

    def test_session_callertype_skips_account_without_primary_handler(self):
        class RuledChar(FakeChar):
            @rule(Look, phase="check")
            def _r(self, action, actor):
                return SKIP

        acct = FakeObj("acct")
        char = RuledChar(key="Hero")
        actor = Actor(account=acct, character=char)
        ctx = ActionContextBuilder().build(
            actor, "look", callertype="session", action_type=Look
        )
        self.assertEqual(ctx.providers, [char])


# --- state gating through the engine ---------------------------------------
class TestStateGating(unittest.TestCase):
    def _actor_with_state(self):
        char = FakeChar()
        actor = Actor(character=char)
        actor.enter_state(TestFlatlined())
        return actor, char

    def test_flatlined_blocks_non_look(self):
        actor, char = self._actor_with_state()
        ctx = ActionContext(providers=actor.state_objects)
        trace = _sync(ENGINE.dispatch(Move(), actor, ctx))
        self.assertEqual(trace.outcome, "blocked")
        self.assertIn("You are flatlined.", char.messages)

    def test_flatlined_passes_look(self):
        actor, char = self._actor_with_state()
        ctx = ActionContext(providers=actor.state_objects)
        trace = _sync(ENGINE.dispatch(Look(), actor, ctx))
        self.assertNotEqual(trace.outcome, "blocked")
        self.assertNotIn("You are flatlined.", char.messages)


# --- DisambiguationState ----------------------------------------------------
class TestDisambiguation(unittest.TestCase):
    def _setup(self, choice_text):
        char = FakeChar()
        actor = Actor(character=char)
        c1, c2 = FakeObj("goblin"), FakeObj("goblin chief")
        pending = Attack()
        pending._raw_string = "attack goblin"
        state = DisambiguationState([c1, c2], pending)
        actor.enter_state(state)
        ctx = ActionContext(providers=[state])
        choice = Choice()
        choice._raw_string = choice_text
        return actor, char, pending, (c1, c2), ctx, choice

    def test_resolves_valid_choice_and_redirects(self):
        actor, char, pending, (c1, c2), ctx, choice = self._setup("2")
        trace = _sync(ENGINE.dispatch(choice, actor, ctx))
        self.assertIs(pending.target, c2)
        self.assertFalse(actor.has_state(DisambiguationState))
        self.assertNotEqual(trace.outcome, "blocked")

    def test_cancels_on_invalid_choice(self):
        actor, char, pending, _cands, ctx, choice = self._setup("99")
        trace = _sync(ENGINE.dispatch(choice, actor, ctx))
        self.assertEqual(trace.outcome, "blocked")
        self.assertIsNone(pending.target)
        self.assertFalse(actor.has_state(DisambiguationState))
        self.assertIn("Invalid choice. Cancelled.", char.messages)

    def test_parser_ambiguous_target_installs_state(self):
        # actor.search raising AmbiguousTarget is the integration trigger.
        class AmbiguousActor(Actor):
            def search(self, name):
                raise AmbiguousTarget(
                    candidates=[FakeObj("goblin"), FakeObj("goblin chief")],
                    original_raw=name,
                )

        char = FakeChar()
        actor = AmbiguousActor(character=char)

        reg = ActionRegistry()
        Attack.__action_verbs__ = ("attack",)
        reg.register(Attack, ("attack",))
        parser = ActionParser(registry=reg)

        with self.assertRaises(AmbiguousTarget) as ctx:
            parser.parse("attack goblin", actor)

        # the dispatch loop (Phase 4) would do exactly this:
        pending = Attack()
        pending._raw_string = "attack goblin"
        actor.enter_state(DisambiguationState(ctx.exception.candidates, pending))
        self.assertTrue(actor.has_state(DisambiguationState))


# --- EvMenuState ------------------------------------------------------------
class TestEvMenuState(unittest.TestCase):
    def test_captures_and_routes_single_node(self):
        char = FakeChar()
        actor = Actor(character=char)
        routed = []

        def node_start(actor_, raw):
            routed.append(raw)
            actor_.msg(f"got {raw}")
            return None  # exit

        menu = EvMenuState({"start": node_start})
        actor.enter_state(menu)
        ctx = ActionContext(providers=[menu])

        choice = Choice()
        choice._raw_string = "hello"
        trace = _sync(ENGINE.dispatch(choice, actor, ctx))

        self.assertEqual(routed, ["hello"])
        self.assertFalse(actor.has_state(EvMenuState))
        self.assertIn("got hello", char.messages)
        self.assertEqual(trace.outcome, "succeeded")

    def test_routes_across_multiple_nodes(self):
        char = FakeChar()
        actor = Actor(character=char)
        routed = []

        def n_start(actor_, raw):
            routed.append(("start", raw))
            return "second"

        def n_second(actor_, raw):
            routed.append(("second", raw))
            return None

        menu = EvMenuState({"start": n_start, "second": n_second})
        actor.enter_state(menu)
        ctx = ActionContext(providers=[menu])

        first = Choice()
        first._raw_string = "a"
        _sync(ENGINE.dispatch(first, actor, ctx))
        self.assertTrue(actor.has_state(EvMenuState))  # still in menu

        second = Choice()
        second._raw_string = "b"
        _sync(ENGINE.dispatch(second, actor, ctx))

        self.assertEqual(routed, [("start", "a"), ("second", "b")])
        self.assertFalse(actor.has_state(EvMenuState))

    def test_menuinput_action_falls_through_capture(self):
        # capture_input must PASS MenuInputAction (else infinite REDIRECT).
        menu = EvMenuState({})
        actor = Actor(character=FakeChar())
        result = menu.capture_input(MenuInputAction(raw="x", menu=menu), actor)
        from evennia.actions.result import PASS

        self.assertIs(result, PASS)


if __name__ == "__main__":
    unittest.main()
