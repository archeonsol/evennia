"""Tests for the EvMore pager's engine-native input capture (EvMoreState).

The pager no longer installs a cmdset; it captures input through an
:class:`~evennia.actions.menus.StateProvider`. These tests route input the way
the cmdhandler bridge does - a raw line dispatched through the real
``RuleEngine`` while the state is active - rather than calling command funcs
directly, so they prove the state actually seizes input across the engine's
before/carry_out REDIRECT loop.
"""

import unittest
from dataclasses import dataclass

from evennia.actions.action import Action
from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext
from evennia.actions.engine import RuleEngine
from evennia.actions.menus import MenuInputAction
from evennia.actions.result import PASS
from evennia.utils.evmore import EvMore, EvMoreState

ENGINE = RuleEngine()


def _sync(d):
    """Extract an already-fired Deferred/coroutine's result, re-raising on failure."""
    import inspect

    from twisted.internet.defer import ensureDeferred

    if inspect.iscoroutine(d):
        # dispatch/engine are now `async def`; drive the (synchronous) coroutine
        # to an already-fired Deferred.
        d = ensureDeferred(d)
    out = {}
    d.addCallbacks(lambda r: out.__setitem__("result", r), lambda f: out.__setitem__("fail", f))
    if "fail" in out:
        out["fail"].raiseException()
    if "result" not in out:
        raise AssertionError("dispatch Deferred did not fire synchronously")
    return out["result"]


class FakeNDB:
    """Bare attribute bag standing in for Evennia's non-persistent handler."""


class FakeCaller:
    def __init__(self, key="Bob"):
        self.key = key
        self.account = None
        self.ndb = FakeNDB()
        self.messages = []
        self.executed = []

    def msg(self, text=None, **kwargs):
        self.messages.append(text)

    def execute_cmd(self, raw_string, session=None, **kwargs):
        self.executed.append((raw_string, session))


class StubMore:
    """Records pager moves so we can assert on routing without real pagination."""

    def __init__(self, caller, session=None):
        self._caller = caller
        self._session = session
        self.calls = []

    def page_next(self):
        self.calls.append("next")

    def page_back(self):
        self.calls.append("back")

    def page_top(self):
        self.calls.append("top")

    def page_end(self):
        self.calls.append("end")

    def page_quit(self, quiet=False):
        self.calls.append("quit")


@dataclass
class _Line(Action):
    """A neutral action carrying a raw input line (mimics the bridge carrier)."""


def _line(raw):
    """A neutral carrier action with the raw line set (as the bridge builds)."""
    action = _Line()
    action._raw_string = raw
    return action


def _dispatch_line(actor, state, raw):
    """Dispatch a raw line through the engine while ``state`` is active."""
    ctx = ActionContext(providers=[state])
    return _sync(ENGINE.dispatch(_line(raw), actor, ctx))


class TestEvMoreStateRouting(unittest.TestCase):
    def setUp(self):
        self.session = object()  # sentinel; the pager and the actor share it
        self.caller = FakeCaller()
        self.actor = Actor(character=self.caller, session=self.session)
        self.more = StubMore(self.caller, session=self.session)
        self.state = EvMoreState(self.more)
        self.actor.enter_state(self.state)

    def test_empty_line_pages_next(self):
        _dispatch_line(self.actor, self.state, "")
        self.assertEqual(self.more.calls, ["next"])

    def test_next_keys_page_next(self):
        for key in ("n", "next", "NEXT"):
            self.more.calls.clear()
            _dispatch_line(self.actor, self.state, key)
            self.assertEqual(self.more.calls, ["next"], key)

    def test_back_keys_page_back(self):
        for key in ("p", "previous"):
            self.more.calls.clear()
            _dispatch_line(self.actor, self.state, key)
            self.assertEqual(self.more.calls, ["back"], key)

    def test_top_and_end_keys(self):
        _dispatch_line(self.actor, self.state, "t")
        _dispatch_line(self.actor, self.state, "end")
        self.assertEqual(self.more.calls, ["top", "end"])

    def test_quit_keys_quit_pager(self):
        for key in ("q", "a", "abort", "quit"):
            self.more.calls.clear()
            _dispatch_line(self.actor, self.state, key)
            self.assertEqual(self.more.calls, ["quit"], key)

    def test_unknown_command_exits_and_refires(self):
        _dispatch_line(self.actor, self.state, "look north")
        self.assertEqual(self.more.calls, ["quit"])
        self.assertEqual(self.caller.executed, [("look north", self.session)])

    def test_menuinput_falls_through_capture(self):
        # capture_input must PASS a MenuInputAction (else infinite REDIRECT).
        result = self.state.capture_input(MenuInputAction(raw="x", menu=self.state), self.actor)
        self.assertIs(result, PASS)

    def test_other_session_input_is_not_captured(self):
        # A different session driving the same body must NOT have its input
        # seized by this pager (session-scoped capture). The line falls through
        # to normal dispatch instead of paging.
        other = Actor(character=self.caller, session=object())
        result = self.state.capture_input(_line("n"), other)
        self.assertIs(result, PASS)
        # and routed through the engine it does not touch the pager:
        _dispatch_line(other, self.state, "n")
        self.assertEqual(self.more.calls, [])

    def test_same_session_input_is_captured(self):
        result = self.state.capture_input(_line("n"), self.actor)
        self.assertIsNot(result, PASS)


class TestEvMoreInstallLifecycle(unittest.TestCase):
    """End-to-end: a real EvMore installs/removes the state and a dispatched
    line drives pagination through the engine (public signature unchanged)."""

    def _make_session(self):
        class FakeProtoFlags:
            def get(self, key, default):
                return default

        class FakeSession:
            protocol_flags = FakeProtoFlags()

        return FakeSession()

    def test_paging_installs_state_and_routes_through_engine(self):
        caller = FakeCaller()
        session = self._make_session()
        caller.sessions = type("S", (), {"get": staticmethod(lambda: [session])})()

        long_text = "\n".join(f"line {i}" for i in range(200))
        EvMore(caller, long_text, session=session)

        actor = Actor(character=caller, session=session)
        self.assertTrue(actor.has_state(EvMoreState))
        more = caller.ndb._more

        # page to the end through the engine, then quit.
        _dispatch_line(actor, actor.state_objects[-1], "e")
        self.assertEqual(more._npos, more._npages - 1)

        _dispatch_line(actor, actor.state_objects[-1], "q")
        self.assertFalse(actor.has_state(EvMoreState))


if __name__ == "__main__":
    unittest.main()
