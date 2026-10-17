"""Tests for the engine-shipped system actions (``evennia.actions.default.system``).

The action-engine analogue of the ``CmdSystems``/``CmdTasks``/``CmdPy``
command tests: dispatch through a real :class:`~evennia.actions.engine.RuleEngine`
with fakes for the scheduler registry, the task handler, and player input
(``@py`` console mode drives the engine's generator suspension).
"""

import datetime
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from twisted.internet import task as twisted_task
from twisted.internet.defer import Deferred

from evennia.actions.action import Action
from evennia.actions.context import ActionContext
from evennia.actions.default import python_console as python_console_module
from evennia.actions.default import system as system_module
from evennia.actions.default.system import (
    CharacterSystemRules,
    Objects,
    Py,
    PyRules,
    Scripts,
    Systems,
    Tasks,
)
from evennia.actions.engine import RuleEngine
from evennia.actions.menus import YesNoState
from evennia.actions.state import get_states
from evennia.actions.tests.fakes import (
    ENGINE,
    FakeChar,
    FakeSession,
    _sync,
    dispatch,
    make_actor,
)
from evennia.authorization.storage import grant_capabilities, principal_refs
from evennia.objects import models as object_models
from evennia.objects.objects import DefaultCharacter
from evennia.scripts import models as script_models
from evennia.scripts import taskhandler as taskhandler_module
from evennia.scripts.models import ScriptDB
from evennia.utils import create
from evennia.utils import systems as systems_module
from evennia.utils.ansi import strip_ansi
from evennia.utils.test_resources import BaseEvenniaTestCase, EvenniaTest

# the package re-exports shadow the engine submodule with the singleton; fetch
# the real module for patching its generator-input seam.
engine_mod = sys.modules["evennia.actions.engine"]


class Wizard(CharacterSystemRules, FakeChar):
    """Provider fixture: a character carrying the system rules."""

    def __init__(self, perms=("Developer",), **kwargs):
        super().__init__(perms=perms, **kwargs)
        self.sessions = SimpleNamespace(all=lambda: [None])

    def has_capability(self, capability):
        """Model explicit R3F grants without touching the database."""

        if self.permissions.get("developer"):
            return capability in {
                "engine.runtime.manage",
                "engine.system.inspect",
            }
        if self.permissions.get("builder"):
            return capability == "engine.system.inspect"
        return False


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

    def test_large_registry_is_sent_in_bounded_pages(self):
        char, actor = _setup(perms=("Builder",))
        registered = [_FakeSystem(f"system-{index}") for index in range(21)]
        with mock.patch.object(systems_module, "all_systems", return_value=registered):
            self._systems(char, actor)

        texts = _texts(char)
        self.assertEqual(len(texts), 3)
        self.assertIn("(1/3)", texts[0])
        self.assertIn("(3/3)", texts[2])
        self.assertIn("system-20", texts[2])

    def test_malformed_entry_does_not_silence_command(self):
        char, actor = _setup(perms=("Builder",))
        malformed = _FakeSystem("broken")
        malformed.cadence = SimpleNamespace(
            describe=mock.Mock(side_effect=ValueError("bad cadence"))
        )
        with (
            mock.patch.object(
                systems_module,
                "all_systems",
                return_value=[malformed, _FakeSystem("healthy")],
            ),
            mock.patch("evennia.utils.logger.log_trace") as log_trace,
        ):
            self._systems(char, actor)

        output = "\n".join(_texts(char))
        self.assertIn("broken", output)
        self.assertIn("<error>", output)
        self.assertIn("healthy", output)
        log_trace.assert_called_once()

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

    def test_unknown_task_action_is_refused_before_asking(self):
        char, actor = _setup()
        handler = _FakeTaskHandler({1: _task_entry()})
        self._tasks(char, actor, raw="1", switches=("foo",), handler=handler)
        self.assertEqual(_FakeTask.calls, [])
        self.assertTrue(any("/foo is not a task action" in m for m in _texts(char)))

    def test_switch_prefix_and_case_are_accepted(self):
        char, actor = _setup()
        handler = _FakeTaskHandler({1: _task_entry(), 2: _task_entry()})
        self._tasks(char, actor, raw="tick", switches=("CANC",), handler=handler)
        self.assertEqual(_FakeTask.calls, [(1, "cancel"), (2, "cancel")])

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


def deferred_job():
    """Module-level task callback, so a persistent task can pickle it."""
    return "success"


class TestTasksOnTheTaskHandler(BaseEvenniaTestCase):
    """``@tasks`` against the real task handler, driven by a test clock."""

    delay = 5

    def setUp(self):
        super().setUp()
        self.handler = taskhandler_module.TASK_HANDLER
        self.old_clock = self.handler.clock
        self.handler.clock = twisted_task.Clock()
        self.handler.clear()
        self.task = self.handler.add(self.delay, deferred_job)
        self.char, self.actor = _setup()

    def tearDown(self):
        self.handler.clear()
        self.handler.clock = self.old_clock
        super().tearDown()

    def _tasks(self, raw="", switches=()):
        self.char.messages.clear()
        action = Tasks.parse(raw, self.actor, switches=switches, verb="@tasks")
        dispatch(action, self.actor, [self.char])
        return "\n".join(_texts(self.char))

    def _answer(self, reply):
        """Send one input line to the pending yes/no question."""
        self.char.messages.clear()
        (state,) = [s for s in get_states(self.char) if isinstance(s, YesNoState)]
        line = Action()
        line._raw_string = reply
        context = ActionContext(providers=[state], actor=self.actor, raw_string=reply)
        _sync(ENGINE.dispatch(line, self.actor, context, record_phases=False))
        return "".join(_texts(self.char))

    def _confirm_prompt(self, switch):
        prompt = self._tasks(str(self.task.get_id()), switches=(switch,))
        self.assertIn(f"{switch.capitalize()} task 1 with completion date", prompt)
        self.assertIn(" (deferred_job) ", prompt)

    def test_listing_shows_a_persistent_task(self):
        self.handler.clear()
        self.handler.add(self.delay, deferred_job, persistent=True)
        self.assertIn("True", self._tasks())

    def test_pause_then_unpause(self):
        self._confirm_prompt("pause")
        self._answer("y")
        self.assertTrue(self.task.paused)
        self.handler.clock.advance(self.delay + 1)
        self.assertTrue(self.task.exists())
        self._confirm_prompt("unpause")
        self._answer("y")
        self.handler.clock.advance(1)
        self.assertFalse(self.task.exists())

    def test_do_task(self):
        self._confirm_prompt("do_task")
        self._answer("y")
        self.assertFalse(self.task.exists())

    def test_remove(self):
        self._confirm_prompt("remove")
        self._answer("y")
        self.assertFalse(self.task.exists())

    def test_call_runs_the_callback_and_keeps_the_task(self):
        self._confirm_prompt("call")
        self._answer("y")
        self.assertTrue(self.task.active())
        self.handler.clock.advance(self.delay + 1)
        self.assertFalse(self.task.exists())

    def test_unanswered_cancel_lets_the_task_complete(self):
        self._tasks(str(self.task.get_id()), switches=("cancel",))
        self.handler.clock.advance(self.delay + 1)
        self.assertFalse(self.task.exists())

    def test_yes_reports_the_action_result(self):
        self._tasks(str(self.task.get_id()), switches=("cancel",))
        self.assertEqual(
            self._answer("y"),
            "cancel request completed.The task function cancel returned: True",
        )
        self.assertTrue(self.task.exists())
        self.assertFalse(self.task.active())

    def test_task_that_completes_while_waiting_is_left_alone(self):
        self._tasks(str(self.task.get_id()), switches=("cancel",))
        self.handler.clock.advance(self.delay + 1)
        self.assertEqual(self._answer("y"), "Task completed while waiting for input.")
        self.assertFalse(self.task.exists())

    def test_new_task_under_the_same_id_is_not_touched(self):
        self._tasks(str(self.task.get_id()), switches=("cancel",))
        self.handler.clock.advance(self.delay + 1)
        self.assertFalse(self.task.exists())
        newer = self.handler.add(self.delay + 1, deferred_job)
        self.assertEqual(newer.get_id(), self.task.get_id())
        self.assertEqual(self._answer("y"), "Task completed while waiting for input.")
        self.assertTrue(newer.active())


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

    def test_clientraw_sends_the_unstringified_result_raw(self):
        char, actor = _setup()
        char.msg = mock.Mock()
        self._py(char, actor, "1+2", switches=("clientraw",))
        shown = [
            call.kwargs["text"][0] for call in char.msg.call_args_list if "text" in call.kwargs
        ]
        self.assertIn(">>> 1+2", shown)
        result = char.msg.call_args_list[-1]
        self.assertEqual(result.args[0], (3, {"type": "py_output"}))
        self.assertTrue(result.kwargs["options"]["client_raw"])

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

        with mock.patch.object(engine_mod, "_get_input_future", side_effect=fake_input):
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

    def test_editor_save_rechecks_runtime_capability(self):
        char, _ = _setup()
        char.db._py_measure_time = False
        char.db._py_clientraw = False
        char.permissions.remove("Developer")
        with mock.patch.object(python_console_module, "run_code_snippet") as run:
            python_console_module.py_code(char, "dangerous()")
        run.assert_not_called()
        self.assertTrue(any("permission" in text for text in _texts(char)))


class TestPyAccountScope(unittest.TestCase):
    def test_effective_guard_lets_account_rules_fire(self):
        # PyRules guards on actor.effective, so an account-shell provider
        # (no puppet) runs @py too.
        class PyAccount(PyRules, FakeChar):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.sessions = SimpleNamespace(all=lambda: [None])

            def has_capability(self, capability):
                return capability == "engine.runtime.manage"

        account = PyAccount(key="acct", perms=("Developer",))
        actor = make_actor(None, account=account)
        action = Py.parse("1+1", actor, verb="@py")
        dispatch(action, actor, [account])
        self.assertTrue(any(m == "2" for m in _texts(account)))


class _SystemAccount(FakeChar):
    """Account principal carrying the new account-scoped system grants."""

    def has_capability(self, capability):
        return capability in {"engine.system.inspect", "engine.script.control"}


def _system_actor():
    """Build a character provider with an authorized account principal."""
    char = Wizard()
    account = _SystemAccount(key="account")
    char.account = account
    return char, make_actor(char, account=account)


class _Query(list):
    """Tiny queryset-shaped list for native system tests."""

    def all(self):
        return self

    def exclude(self, **kwargs):
        return self

    def filter(self, **kwargs):
        result = self
        if "db_obj" in kwargs:
            result = [script for script in result if script.obj is kwargs["db_obj"]]
        if "db_key__iexact" in kwargs:
            result = [
                script
                for script in result
                if script.key.lower() == kwargs["db_key__iexact"].lower()
            ]
        if "db_typeclass_path__iendswith" in kwargs:
            result = [
                script
                for script in result
                if script.typeclass_path.endswith(kwargs["db_typeclass_path__iendswith"])
            ]
        return _Query(result)

    def order_by(self, *args):
        return self

    def count(self):
        return len(self)


class TestObjects(unittest.TestCase):
    """Native ``@objects`` statistics use the dedicated inspect grant."""

    def test_totals_and_recent_objects(self):
        char, actor = _system_actor()
        recent = SimpleNamespace(
            date_created=datetime.datetime(2026, 1, 1),
            dbref="#9",
            key="box",
            path="game.Box",
        )
        manager = mock.Mock()
        manager.count.return_value = 1
        manager.get_typeclass_totals.return_value = [
            {"typeclass": "game.Box", "count": 1, "percent": 100.0}
        ]
        manager.all.return_value = _Query([recent])
        fake_db = SimpleNamespace(objects=manager)
        family = SimpleNamespace(objects=SimpleNamespace(all_family=lambda: _Query()))
        with (
            mock.patch.object(object_models, "ObjectDB", fake_db),
            mock.patch("evennia.utils.utils.class_from_module", return_value=family),
        ):
            dispatch(Objects.parse("5", actor, verb="@objects"), actor, [char])
        output = "\n".join(_texts(char))
        self.assertIn("Object subtype totals", output)
        self.assertIn("game.Box", output)
        self.assertIn("box", output)


class TestScripts(unittest.TestCase):
    """Native storage-script lookup, creation, attachment, and deletion."""

    def _run(self, raw="", switches=(), scripts=()):
        char, actor = _system_actor()
        manager = mock.Mock()
        query = _Query(scripts)
        manager.all.return_value = query
        manager.filter.side_effect = lambda **kwargs: query.filter(**kwargs)
        manager.get_all_scripts.side_effect = lambda query_: query
        fake_db = SimpleNamespace(objects=manager)
        action = Scripts.parse(raw, actor, switches=switches, verb="@scripts")
        with (
            mock.patch.object(script_models, "ScriptDB", fake_db),
            mock.patch.object(system_module, "_show_scripts") as pager,
        ):
            trace = dispatch(action, actor, [char])
        return char, actor, trace, pager

    def test_lists_scripts_with_native_pager(self):
        script = SimpleNamespace(key="store", typeclass_path="game.Store", obj=None)
        _, _, _, pager = self._run(scripts=[script])
        pager.assert_called_once()

    def test_creates_global_storage_script(self):
        char, actor = _system_actor()
        manager = mock.Mock()
        manager.filter.return_value = _Query()
        fake_db = SimpleNamespace(objects=manager)
        created = SimpleNamespace(key="store", typeclass_path="game.Store")
        action = Scripts.parse("store:game.Store", actor, verb="@scripts")
        with (
            mock.patch.object(script_models, "ScriptDB", fake_db),
            mock.patch("evennia.utils.create.create_script", return_value=created),
            mock.patch.object(system_module, "_show_scripts"),
        ):
            dispatch(action, actor, [char])
        self.assertTrue(any("Global Script Created" in text for text in _texts(char)))

    def test_multiple_delete_requires_confirmation(self):
        scripts = [
            SimpleNamespace(
                key=f"store-{index}",
                typeclass_path="game.Store",
                obj=None,
                delete=mock.Mock(),
            )
            for index in range(2)
        ]
        char, actor = _system_actor()
        query = _Query(scripts)
        manager = mock.Mock()
        manager.filter.return_value = query
        fake_db = SimpleNamespace(objects=manager)
        answer = Deferred()
        answer.callback("no")
        action = Scripts.parse("game.Store", actor, switches=("del",), verb="@scripts")
        with (
            mock.patch.object(script_models, "ScriptDB", fake_db),
            mock.patch.object(engine_mod, "_get_input_future", return_value=answer),
        ):
            dispatch(action, actor, [char])
        self.assertTrue(any("Aborted" in text for text in _texts(char)))
        for script in scripts:
            script.delete.assert_not_called()

    def test_missing_object_does_not_fall_through_to_global_create(self):
        char, actor = _system_actor()
        manager = mock.Mock()
        manager.filter.return_value = _Query()
        fake_db = SimpleNamespace(objects=manager)
        action = Scripts.parse("missing = store:game.Store", actor, verb="@scripts")
        with (
            mock.patch.object(script_models, "ScriptDB", fake_db),
            mock.patch("evennia.utils.create.create_script") as create_script,
        ):
            dispatch(action, actor, [char])
        create_script.assert_not_called()

    def test_lone_key_lookup_finds_existing_script(self):
        char, actor = _system_actor()
        script = SimpleNamespace(key="store", typeclass_path="game.Storage", obj=None)
        query = _Query([script])
        manager = mock.Mock()
        manager.filter.side_effect = lambda **kwargs: query.filter(**kwargs)
        fake_db = SimpleNamespace(objects=manager)
        action = Scripts.parse("store", actor, verb="@scripts")
        with (
            mock.patch.object(script_models, "ScriptDB", fake_db),
            mock.patch.object(system_module, "_show_scripts") as pager,
            mock.patch("evennia.utils.create.create_script") as create_script,
        ):
            dispatch(action, actor, [char])
        pager.assert_called_once()
        create_script.assert_not_called()

    def test_invalid_confirmation_reprompts_and_revocation_aborts(self):
        scripts = [
            SimpleNamespace(
                key=f"store-{index}", typeclass_path="game.Store", obj=None, delete=mock.Mock()
            )
            for index in range(2)
        ]
        char, actor = _system_actor()
        query = _Query(scripts)
        manager = mock.Mock()
        manager.filter.return_value = query
        fake_db = SimpleNamespace(objects=manager)
        answers = iter(["maybe", "yes"])

        def input_future(actor_, prompt):
            deferred = Deferred()
            answer = next(answers)
            if answer == "yes":
                actor_.account.has_capability = lambda capability: False
            deferred.callback(answer)
            return deferred

        action = Scripts.parse("game.Store", actor, switches=("delete",), verb="@scripts")
        with (
            mock.patch.object(script_models, "ScriptDB", fake_db),
            mock.patch.object(engine_mod, "_get_input_future", side_effect=input_future),
        ):
            dispatch(action, actor, [char])
        self.assertTrue(any("Please answer" in text for text in _texts(char)))
        self.assertTrue(any("permission" in text for text in _texts(char)))
        for script in scripts:
            script.delete.assert_not_called()


class SystemCharacter(CharacterSystemRules, DefaultCharacter):
    """A database character carrying the default system rules."""


class TestScriptsOnTheDatabase(EvenniaTest):
    """``@scripts`` against stored scripts and objects."""

    character_typeclass = SystemCharacter
    evennia_fixtures = {"char1", "obj1"}

    def setUp(self):
        super().setUp()
        grant_capabilities(
            principal_refs(self.char1)[0],
            ["engine.object.edit"],
            scope_kind="world",
            scope_key="*",
            provenance="test",
        )

    def _scripts(self, raw, switches=(), answer=None):
        actor = make_actor(self.char1, account=self.account)
        action = Scripts.parse(raw, actor, switches=switches, verb="@scripts")
        self.char1.msg = mock.Mock()
        reply = Deferred()
        reply.callback(answer)
        # The pager only pages to a connected session.
        with (
            mock.patch.object(engine_mod, "_get_input_future", return_value=reply) as asked,
            mock.patch.object(self.char1.sessions, "get", return_value=[FakeSession()]),
        ):
            dispatch(action, actor, [self.char1])
        shown = []
        for call in self.char1.msg.call_args_list:
            text = call.kwargs.get("text", call.args[0] if call.args else "")
            shown.append(strip_ansi(str(text[0] if isinstance(text, tuple) else text)))
        del self.char1.msg
        return "\n".join(shown), asked.called

    def _on_obj(self):
        return ScriptDB.objects.filter(db_obj=self.obj1)

    def test_attach_list_and_delete_on_an_object(self):
        shown, _ = self._scripts("Obj =")
        self.assertIn("No scripts defined on Obj", shown)
        shown, _ = self._scripts("Obj = scripts.scripts.DefaultScript")
        self.assertIn("Script scripts.scripts.DefaultScript successfully added to Obj.", shown)
        self.assertEqual(self._on_obj().count(), 1)
        shown, _ = self._scripts("Obj =")
        self.assertIn("dbref", shown)
        shown, asked = self._scripts(
            "Obj = scripts.scripts.DefaultScript", switches=("delete",), answer="y"
        )
        self.assertFalse(asked)
        self.assertIn("Script on Obj Deleted - ", shown)
        self.assertFalse(self._on_obj().exists())

    def test_invalid_typeclass_path_creates_nothing(self):
        before = ScriptDB.objects.count()
        shown, _ = self._scripts("evennia.Dummy")
        self.assertIn("Global Script NOT Created (see log)", shown)
        self.assertEqual(ScriptDB.objects.count(), before)

    def test_create_and_delete_a_global_script(self):
        shown, _ = self._scripts("evennia.scripts.scripts.DoNothing")
        self.assertIn("Global Script Created - sys_do_nothing ", shown)
        path = "evennia.scripts.scripts.DoNothing"
        self.assertTrue(ScriptDB.objects.filter(db_typeclass_path=path).exists())
        shown, _ = self._scripts(path, switches=("delete",))
        self.assertIn("Global Script Deleted - sys_do_nothing", shown)
        self.assertFalse(ScriptDB.objects.filter(db_typeclass_path=path).exists())

    def test_confirmed_range_delete(self):
        first = create.create_script(key="script1")
        second = create.create_script(key="script2")
        third = create.create_script(key="script3")
        shown, asked = self._scripts(f"#{first.id}-#{third.id}", switches=("delete",), answer="y")
        self.assertTrue(asked)
        for key in ("script1", "script2", "script3"):
            self.assertIn(
                f"Global Script Deleted - {key} (evennia.scripts.scripts.DefaultScript)", shown
            )
        self.assertFalse(ScriptDB.objects.filter(id__in=(first.id, second.id, third.id)).exists())


if __name__ == "__main__":
    unittest.main()
