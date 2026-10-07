"""close_captures ends every input capture on a holder."""

import asyncio
import sys
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest import mock

from twisted.internet.defer import ensureDeferred

from evennia.actions.action import Action
from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext
from evennia.actions.engine import RuleEngine
from evennia.actions.menus import GetInputState, InputCaptureState, MenuPrompt, YesNoState
from evennia.actions.result import CLAIM
from evennia.actions.rule import rule
from evennia.actions.state import (
    StateProvider,
    close_captures,
    enter_state,
    get_states,
    has_state,
)
from evennia.utils.eveditor import EvEditorState
from evennia.utils.evmore import EvMoreState

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

    def test_an_editor_quits_without_saving(self):
        holder = _holder()
        editor = mock.Mock()
        enter_state(holder, EvEditorState(editor))
        self.assertTrue(close_captures(holder))
        editor.quit.assert_called_once_with()
        editor.save_buffer.assert_not_called()

    def test_other_states_stay(self):
        holder = _holder()
        other = StateProvider()
        enter_state(holder, other)
        enter_state(holder, GetInputState(holder, "Name?", mock.Mock()))
        close_captures(holder)
        self.assertEqual(get_states(holder), [other])


class TestCloseSuspendedFlow(unittest.TestCase):
    def _suspend(self, flow):
        holder = _holder()
        actor = Actor(character=holder)
        dispatch = ensureDeferred(
            RuleEngine().dispatch(Ask(), actor, ActionContext(providers=[flow]))
        )
        return holder, dispatch

    def _close(self, prompt):
        loop = asyncio.new_event_loop()
        flow = AskFlow(prompt)
        try:
            with (
                mock.patch.object(engine_mod.clock, "get_bound_loop", return_value=loop),
                mock.patch("evennia.utils.logger.log_trace") as rule_error,
            ):
                holder, dispatch = self._suspend(flow)
                self.assertTrue(has_state(holder, InputCaptureState))
                self.assertFalse(dispatch.called)
                self.assertTrue(close_captures(holder))
                trace = loop.run_until_complete(dispatch.asFuture(loop))
        finally:
            loop.close()
        self.assertEqual(trace.outcome, "succeeded")
        rule_error.assert_not_called()
        return holder, flow

    def test_a_text_prompt_flow_is_closed(self):
        holder, flow = self._close("Proceed?")
        self.assertEqual(flow.events, ["closed"])
        self.assertEqual(get_states(holder), [])

    def test_a_menu_flow_is_closed(self):
        holder, flow = self._close(MenuPrompt("Pick:", options=[("a", "Alpha")]))
        self.assertEqual(flow.events, ["closed"])
        self.assertEqual(get_states(holder), [])
