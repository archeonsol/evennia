"""close_captures ends every input capture on a holder."""

import asyncio
import sys
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest import mock

from twisted.internet.defer import Deferred, ensureDeferred

from evennia.actions.action import Action
from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext
from evennia.actions.engine import RuleEngine
from evennia.actions.menus import (
    INPUT_CLOSED,
    GetInputState,
    InputCaptureState,
    MenuPrompt,
    YesNoState,
)
from evennia.actions.result import CLAIM, PASS
from evennia.actions.rule import rule
from evennia.actions.state import (
    StateProvider,
    close_captures,
    enter_state,
    get_states,
    has_state,
)
from evennia.commands.default.tests import BaseEvenniaCommandTest
from evennia.utils.eveditor import EvEditor, EvEditorState
from evennia.utils.evmore import EvMore, EvMoreState

engine_mod = sys.modules["evennia.actions.engine"]


def _holder():
    messages = []
    return SimpleNamespace(ndb=SimpleNamespace(), msg=lambda text=None, **kw: messages.append(text))


@dataclass
class Ask(Action):
    pass


class AskFlow:
    """An ``@interactive``-shaped carry_out rule that records how it ends."""

    def __init__(self, prompt):
        self.prompt = prompt
        self.events = []

    @rule(Ask, phase="carry_out")
    def ask(self, action, actor):
        try:
            answer = yield self.prompt
            self.events.append(f"got:{answer}")
        except GeneratorExit:
            self.events.append("closed")
            raise
        return CLAIM


class AfterFlow:
    """A lower-priority carry_out rule and a report rule that record when they run."""

    def __init__(self):
        self.events = []

    @rule(Ask, phase="carry_out", priority=-10)
    def fallback(self, action, actor):
        self.events.append("carry_out")
        return PASS

    @rule(Ask, phase="report")
    def narrate(self, action, actor):
        self.events.append("report")


class WaitFlow:
    """A flow suspended on a Deferred, the shape of an editor handoff."""

    def __init__(self):
        self.waiter = Deferred()
        self.events = []

    @rule(Ask, phase="carry_out")
    def wait(self, action, actor):
        try:
            text = yield self.waiter
            self.events.append(f"got:{text}")
        except GeneratorExit:
            self.events.append("closed")
            raise
        return CLAIM


class TestCloseCaptures(unittest.TestCase):
    def test_nothing_open_closes_nothing(self):
        holder = _holder()
        enter_state(holder, StateProvider())
        self.assertFalse(close_captures(holder))
        self.assertEqual(len(get_states(holder)), 1)

    def test_a_prompt_exits_without_its_callback(self):
        holder = _holder()
        callback = mock.Mock()
        enter_state(holder, GetInputState(holder, "Name?", callback))
        self.assertTrue(close_captures(holder))
        self.assertFalse(has_state(holder, GetInputState))
        callback.assert_not_called()

    def test_a_yes_no_question_exits_unanswered(self):
        holder = _holder()
        yes, no = mock.Mock(), mock.Mock()
        enter_state(holder, YesNoState(holder, "Sure?", yes, no))
        self.assertTrue(close_captures(holder))
        self.assertFalse(has_state(holder, YesNoState))
        yes.assert_not_called()
        no.assert_not_called()

    def test_a_pager_exits_without_its_exit_command(self):
        holder = _holder()
        more = mock.Mock()
        enter_state(holder, EvMoreState(more))
        self.assertTrue(close_captures(holder))
        self.assertFalse(has_state(holder, EvMoreState))
        more.page_quit.assert_not_called()

    def test_other_states_stay(self):
        holder = _holder()
        other = StateProvider()
        enter_state(holder, other)
        enter_state(holder, GetInputState(holder, "Name?", mock.Mock()))
        close_captures(holder)
        self.assertEqual(get_states(holder), [other])


class TestCloseSuspendedFlow(unittest.TestCase):
    def _suspend(self, *providers):
        holder = _holder()
        actor = Actor(character=holder)
        dispatch = ensureDeferred(
            RuleEngine().dispatch(Ask(), actor, ActionContext(providers=list(providers)))
        )
        return holder, dispatch

    def _close(self, prompt):
        loop = asyncio.new_event_loop()
        flow = AskFlow(prompt)
        after = AfterFlow()
        try:
            with (
                mock.patch.object(engine_mod.clock, "get_bound_loop", return_value=loop),
                mock.patch("evennia.utils.logger.log_trace") as rule_error,
            ):
                holder, dispatch = self._suspend(flow, after)
                self.assertTrue(has_state(holder, InputCaptureState))
                self.assertFalse(dispatch.called)
                self.assertTrue(close_captures(holder))
                trace = loop.run_until_complete(dispatch.asFuture(loop))
        finally:
            loop.close()
        self.assertEqual(trace.outcome, "aborted")
        self.assertEqual(after.events, [])
        rule_error.assert_not_called()
        return holder, flow

    def test_a_flow_waiting_on_a_closed_deferred_ends_the_action(self):
        flow = WaitFlow()
        after = AfterFlow()
        holder, dispatch = self._suspend(flow, after)
        self.assertFalse(dispatch.called)
        flow.waiter.callback(INPUT_CLOSED)
        self.assertTrue(dispatch.called)
        self.assertEqual(flow.events, ["closed"])
        self.assertEqual(after.events, [])
        self.assertEqual(dispatch.result.outcome, "aborted")

    def test_a_text_prompt_flow_is_closed(self):
        holder, flow = self._close("Proceed?")
        self.assertEqual(flow.events, ["closed"])
        self.assertEqual(get_states(holder), [])

    def test_a_menu_flow_is_closed(self):
        holder, flow = self._close(MenuPrompt("Pick:", options=[("a", "Alpha")]))
        self.assertEqual(flow.events, ["closed"])
        self.assertEqual(get_states(holder), [])


def _record_save(caller, buf):
    caller.ndb.editor_calls.append("save")
    return True


def _record_quit(caller):
    caller.ndb.editor_calls.append("quit")


def _record_close(caller):
    caller.ndb.editor_calls.append("close")


class TestCloseRealEditor(BaseEvenniaCommandTest):
    """close_captures on a live EvEditor and EvMore, not a stand-in."""

    def _editor(self, **kwargs):
        # Module-level hooks, so a persistent editor can pickle them.
        calls = self.char1.ndb.editor_calls = []
        editor = EvEditor(
            self.char1,
            savefunc=_record_save,
            quitfunc=_record_quit,
            closefunc=_record_close,
            **kwargs,
        )
        return editor, calls

    def test_a_line_editor_closes_without_save_or_quitfunc(self):
        editor, calls = self._editor()
        editor.update_buffer("unsaved draft")
        self.assertTrue(close_captures(self.char1))
        self.assertEqual(calls, ["close"])
        self.assertIsNone(self.char1.ndb._eveditor)
        self.assertFalse(has_state(self.char1, EvEditorState))

    def test_a_closed_persistent_editor_is_not_rebuilt_on_reload(self):
        editor, calls = self._editor(persistent=True)
        self.assertIsNotNone(self.char1.attributes.get("_eveditor_saved"))
        close_captures(self.char1)
        self.assertIsNone(self.char1.attributes.get("_eveditor_saved"))
        self.assertIsNone(self.char1.attributes.get("_eveditor_buffer_temp"))

    def test_a_normal_quit_still_runs_quitfunc_not_closefunc(self):
        editor, calls = self._editor()
        editor.quit()
        self.assertEqual(calls, ["quit"])

    def test_closing_a_web_editor_dismisses_its_panel(self):
        for sess in self.char1.sessions.all():
            sess.protocol_flags["CLIENT_EDITOR"] = True
        editor, calls = self._editor()
        self.char1.msg = mock.Mock()
        editor.close()
        sent = [kw for _n, _a, kw in self.char1.msg.mock_calls if "editor_close" in kw]
        self.assertEqual(len(sent), 1)
        self.assertEqual(calls, ["close"])
        self.assertIsNone(self.char1.ndb._eveditor)

    def test_a_closed_pager_drops_its_handle(self):
        EvMore(self.char1, "\n".join(f"line {n}" for n in range(200)), session=self.session)
        self.assertIsNotNone(self.char1.ndb._more)
        self.assertTrue(close_captures(self.char1))
        self.assertIsNone(self.char1.ndb._more)
        self.assertFalse(has_state(self.char1, EvMoreState))
