"""Tests for the engine-shipped system actions (``evennia.actions.default.system``).

The action-engine analogue of the ``CmdSystems``/``CmdTasks``/``CmdPy``
command tests: dispatch through a real :class:`~evennia.actions.engine.RuleEngine`
with fakes for the scheduler registry, the task handler, and player input
(``@py`` console mode drives the engine's generator suspension).
"""

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from twisted.internet.defer import Deferred

from evennia.actions.default import system as system_module
from evennia.actions.default.system import (
    CharacterSystemRules,
    Py,
    PyRules,
    Systems,
    Tasks,
)
from evennia.actions.engine import RuleEngine
from evennia.actions.tests.fakes import FakeChar, dispatch, make_actor
from evennia.scripts import taskhandler as taskhandler_module
from evennia.utils import systems as systems_module

# the package re-exports shadow the engine submodule with the singleton; fetch
# the real module for patching its generator-input seam.
engine_mod = sys.modules["evennia.actions.engine"]


class Wizard(CharacterSystemRules, FakeChar):
    """Provider fixture: a character carrying the system rules."""

    def __init__(self, perms=("Developer",), **kwargs):
        super().__init__(perms=perms, **kwargs)
        self.sessions = SimpleNamespace(all=lambda: [None])


def _setup(perms=("Developer",)):
    char = Wizard(perms=perms)
    actor = make_actor(char)
    return char, actor


def _texts(char):
    """Flatten message captures (some are ``(text, {opts})`` tuples)."""
    return [m[0] if isinstance(m, tuple) else str(m) for m in char.messages]


# --- @systems --------------------------------------------------------------------
class _FakeSystem:
    def __init__(self, name="heartbeat"):
        self.name = name
        self.cadence = SimpleNamespace(describe=lambda: "every 10s")
        self.scope = SimpleNamespace(describe=lambda: "global")
        self.last_run = None
        self.fire_count = 3
        self.in_flight = False


class TestSystems(unittest.TestCase):
    def _systems(self, char, actor):
        action = Systems.parse("", actor, verb="@systems")
        return dispatch(action, actor, [char])

    def test_lists_registered_systems(self):
        char, actor = _setup(perms=("Builder",))
        with mock.patch.object(systems_module, "all_systems", return_value=[_FakeSystem()]):
            self._systems(char, actor)
        out = "\n".join(_texts(char))
        self.assertIn("Registered systems", out)
        self.assertIn("heartbeat", out)
        self.assertIn("every 10s", out)

    def test_no_systems_registered(self):
        char, actor = _setup(perms=("Builder",))
        with mock.patch.object(systems_module, "all_systems", return_value=[]):
            self._systems(char, actor)
        self.assertTrue(any("No systems" in m for m in _texts(char)))

    def test_gated_for_player(self):
        char, actor = _setup(perms=("Player",))
        trace = self._systems(char, actor)
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @tasks ----------------------------------------------------------------------
def tick():
    """Stand-in deferred function for task entries (short name: table cells wrap)."""


class _FakeTaskHandler:
    def __init__(self, tasks=None):
        self.tasks = dict(tasks or {})

    def get_deferred(self, task_id):
        return None


class _FakeTask:
    """Stand-in for ``TaskHandlerTask``; records the action methods called."""

    calls = []

    def __init__(self, task_id):
        self.task_id = task_id

    def exists(self):
        return True

    def _record(self, name):
        type(self).calls.append((self.task_id, name))
        return f"{name}-ok"

    def pause(self):
        return self._record("pause")

    def cancel(self):
        return self._record("cancel")

    def remove(self):
        return self._record("remove")


def _task_entry():
    return ("2026-06-12 10:00:00", tick, (), {}, False, None)


class TestTasks(unittest.TestCase):
    def setUp(self):
        _FakeTask.calls = []

    def _tasks(self, char, actor, raw="", switches=(), handler=None):
        action = Tasks.parse(raw, actor, switches=switches, verb="@tasks")
        patches = [
            mock.patch.object(taskhandler_module, "TASK_HANDLER", handler or _FakeTaskHandler()),
            mock.patch.object(taskhandler_module, "TaskHandlerTask", _FakeTask),
        ]
        with patches[0], patches[1]:
            return dispatch(action, actor, [char])

    def test_no_active_tasks(self):
        char, actor = _setup()
        self._tasks(char, actor)
        self.assertTrue(any("no active tasks" in m for m in _texts(char)))

    def test_lists_tasks(self):
        char, actor = _setup()
        handler = _FakeTaskHandler({1: _task_entry()})
        self._tasks(char, actor, handler=handler)
        out = "\n".join(_texts(char))
        self.assertIn("tick", out)
        self.assertIn("Actions:", out)

    def test_action_by_function_name(self):
        char, actor = _setup()
        handler = _FakeTaskHandler({1: _task_entry(), 2: _task_entry()})
        self._tasks(char, actor, raw="tick", switches=("cancel",), handler=handler)
        self.assertEqual(_FakeTask.calls, [(1, "cancel"), (2, "cancel")])
        self.assertTrue(any("completed on task ID 1" in m for m in _texts(char)))

    def test_action_by_unknown_function_name(self):
        char, actor = _setup()
        handler = _FakeTaskHandler({1: _task_entry()})
        self._tasks(char, actor, raw="bogus_func", switches=("cancel",), handler=handler)
        self.assertTrue(any("No tasks deferring" in m for m in _texts(char)))

    def test_action_by_id_asks_confirmation(self):
        char, actor = _setup()
        handler = _FakeTaskHandler({1: _task_entry()})
        asked = {}

        def fake_ask(caller, prompt="", yes_action=None, no_action=None, **kwargs):
            asked["prompt"] = prompt
            yes_action(caller)

        with mock.patch.object(system_module, "ask_yes_no", side_effect=fake_ask):
            self._tasks(char, actor, raw="1", switches=("cancel",), handler=handler)
        self.assertIn("Cancel task 1", asked["prompt"])
        self.assertEqual(_FakeTask.calls, [(1, "cancel")])
        self.assertTrue(any("request completed" in m for m in _texts(char)))

    def test_malformed_request(self):
        char, actor = _setup()
        handler = _FakeTaskHandler({1: _task_entry()})
        self._tasks(char, actor, raw="", switches=("cancel",), handler=handler)
        self.assertTrue(any("misformed" in m for m in _texts(char)))

    def test_gated_for_builder(self):
        char, actor = _setup(perms=("Builder",))
        trace = self._tasks(char, actor)
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


# --- @py -------------------------------------------------------------------------
class TestPy(unittest.TestCase):
    def _py(self, char, actor, raw, switches=()):
        action = Py.parse(raw, actor, switches=switches, verb="@py")
        return dispatch(action, actor, [char])

    def test_snippet_eval(self):
        char, actor = _setup()
        self._py(char, actor, "1+1")
        out = _texts(char)
        self.assertTrue(any(">>> 1+1" in m for m in out))
        self.assertTrue(any(m == "2" for m in out))

    def test_snippet_locals_include_me(self):
        char, actor = _setup()
        self._py(char, actor, "me.key")
        self.assertTrue(any("Staff" in m for m in _texts(char)))

    def test_time_switch_reports_runtime(self):
        char, actor = _setup()
        self._py(char, actor, "1+1", switches=("time",))
        self.assertTrue(any("runtime" in m for m in _texts(char)))

    def test_edit_switch_opens_editor_and_captures_through_engine(self):
        # @py/edit launches EvEditor from inside the carry_out rule. The editor's
        # capture is an engine StateProvider, so a line dispatched afterwards
        # through the same engine must reach the editor buffer - proving the
        # launch-from-carry_out path end to end (no stubbed editor).
        from evennia.actions.action import Action
        from evennia.actions.context import ActionContext
        from evennia.utils.eveditor import EvEditorState

        char, actor = _setup()
        self._py(char, actor, "", switches=("edit",))
        self.assertTrue(actor.has_state(EvEditorState))

        state = actor.state_objects[-1]
        line = Action()
        line._raw_string = "my_var = 42"
        ctx = ActionContext(providers=[state], actor=actor, raw_string="my_var = 42")
        from evennia.actions.tests.fakes import ENGINE, _sync

        _sync(ENGINE.dispatch(line, actor, ctx, record_phases=False))
        self.assertIn("my_var = 42", char.ndb._eveditor.get_buffer())

    def test_console_mode_runs_until_exit(self):
        char, actor = _setup()
        answers = iter(["1+1", "exit"])

        def fake_input(actor_, prompt):
            d = Deferred()
            if prompt:
                char.msg(prompt)
            d.callback(next(answers))
            return d

        with mock.patch.object(engine_mod, "_get_input_deferred", side_effect=fake_input):
            self._py(char, actor, "")
        out = _texts(char)
        self.assertTrue(any("Evennia Interactive Python mode" in m for m in out))
        self.assertTrue(any(">>> 1+1" in m for m in out))
        self.assertTrue(any("2" == m for m in out))
        self.assertTrue(any("Closing the Python console" in m for m in out))

    def test_gated_for_builder(self):
        char, actor = _setup(perms=("Builder",))
        trace = self._py(char, actor, "1+1")
        self.assertFalse(char.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


class TestPyAccountScope(unittest.TestCase):
    def test_effective_guard_lets_account_rules_fire(self):
        # PyRules guards on actor.effective, so an account-shell provider
        # (no puppet) runs @py too.
        class PyAccount(PyRules, FakeChar):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.sessions = SimpleNamespace(all=lambda: [None])

        account = PyAccount(key="acct", perms=("Developer",))
        actor = make_actor(None, account=account)
        action = Py.parse("1+1", actor, verb="@py")
        dispatch(action, actor, [account])
        self.assertTrue(any(m == "2" for m in _texts(account)))


if __name__ == "__main__":
    unittest.main()
