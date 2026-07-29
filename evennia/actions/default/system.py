"""
Default system actions: engine-shipped inspection and developer verbs.

The action-engine analogue of ``evennia/commands/default/system.py``'s
``CmdSystems``/``CmdTasks``/``CmdPy``:

* :class:`Systems` — list scheduler systems (cadence, scope, last fire,
  in-flight), Builder-gated.
* :class:`Tasks` — display or manipulate active ``utils.delay`` tasks,
  Developer-gated. The single-task (by-id) path confirms through the engine's
  :func:`~evennia.actions.menus.ask_yes_no` capture state, exactly as the
  stock command already does in this fork.
* :class:`Py` — execute a Python snippet, or open the interactive Python
  console (a generator ``carry_out`` rule: the engine's generator driver
  interprets ``yield prompt`` as ask-and-suspend, the same contract the stock
  ``@interactive`` command used). Developer-gated. The ``/edit`` switch opens
  the ``EvEditor`` in code mode; the editor's input capture is an engine
  :class:`~evennia.actions.state.StateProvider`, so it captures on the dispatch
  path.

:class:`PyRules` is standalone (guarding ``self is actor.effective``) so the
account shell composes it too, mirroring stock ``CmdPy`` living in both the
Character and Account cmdsets.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.conf import settings

from evennia.objects.character import DefaultCharacter

from ..action import action
from ..menus import ask_yes_no
from ..muxargs import ArgAction
from ..predicate import HasCapability
from ..result import CLAIM, SKIP
from ..rule import rule

__all__ = [
    "Systems",
    "Tasks",
    "Py",
    "Objects",
    "Scripts",
    "ScriptEvMore",
    "PyRules",
    "CharacterSystemRules",
]


@action("@systems")
@dataclass
class Systems(ArgAction):
    """List systems registered with the system scheduler (``@systems``)."""

    __primary_handler__ = DefaultCharacter


@action("@tasks", "@delays", "@task")
@dataclass
class Tasks(ArgAction):
    """Display or terminate active delayed tasks.

    ``@tasks[/pause|/unpause|/do_task|/call|/remove|/cancel]
    [task_id or function_name]``.
    """

    __primary_handler__ = DefaultCharacter


@action("@py", "@!")
@dataclass
class Py(ArgAction):
    """Execute Python code, or open the interactive console.

    ``@py [code]`` / ``@py/time <code>`` / ``@py/clientraw <code>`` /
    ``@py/noecho``. Without code, opens the in-game Python console.
    """

    __primary_handler__ = DefaultCharacter

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        # code is free text: skip the mux lhs/rhs split (an `=` is assignment,
        # not a delimiter).
        return cls(
            args=(raw_args or "").strip(),
            switches=tuple(switches or ()),
            verb=(verb or ""),
        )


@action("@objects")
@dataclass
class Objects(ArgAction):
    """Show object totals, typeclass distribution, and recently created objects."""

    __primary_handler__ = DefaultCharacter


@action("@scripts", "@script")
@dataclass
class Scripts(ArgAction):
    """List, create, attach, inspect, or delete storage scripts."""

    __primary_handler__ = DefaultCharacter


def _show_scripts(caller, scripts, session=None):
    """Open the lazily imported native storage-script pager."""
    from .script_paging import ScriptEvMore

    return ScriptEvMore(caller, scripts, session=session)


def _coll_date_func(task):
    """Normalize a task tuple's completion date and callback memory reference."""
    t_comp_date = str(task[0]).replace("-", "/")
    t_func_name = str(task[1]).split(" ")
    t_func_mem_ref = t_func_name[3] if len(t_func_name) >= 4 else None
    return t_comp_date, t_func_mem_ref


def _make_task_action(caller, task_id, t_comp_date, t_func_mem_ref, task_action, action_request):
    """Build the confirmed-action callback for a single-task ``@tasks`` request.

    Closure-based (no state on the shared provider instance): re-verifies the
    task still exists and is the same task before firing, as the stock command
    did via attributes on its per-call command instance.
    """

    def do_task_action(*args, **kwargs):
        from evennia.scripts import taskhandler

        task_args = taskhandler.TASK_HANDLER.tasks.get(task_id, False)
        if not task_args:
            caller.msg("Task completed while waiting for input.")
            return
        comp_date, func_mem_ref = _coll_date_func(task_args)
        if t_comp_date != comp_date or t_func_mem_ref != func_mem_ref:
            caller.msg("Task completed while waiting for input.")
            return
        action_return = task_action()
        caller.msg(f"{action_request} request completed.")
        caller.msg(f"The task function {action_request} returned: {action_return}")

    return do_task_action


class PyRules:
    """Baseline ``@py`` rule; composable into both character and account shells."""

    @rule(Py, phase="carry_out", requires=HasCapability("engine.runtime.manage"))
    def carry_out_py(self, action, actor):
        if self is not getattr(actor, "effective", None):
            return SKIP
        caller = self
        pycode = action.args
        switches = action.switches

        if "edit" in switches:
            # Open the EvEditor in code mode (its input capture is an engine
            # StateProvider, so it works on the dispatch path). Reuse the stock
            # module-level load/save/quit funcs - they are picklable, which the
            # persistent editor requires.
            from evennia.utils.eveditor import EvEditor

            from .python_console import py_code, py_load, py_quit

            caller.db._py_measure_time = "time" in switches
            caller.db._py_clientraw = "clientraw" in switches
            EvEditor(
                caller,
                loadfunc=py_load,
                savefunc=py_code,
                quitfunc=py_quit,
                key="Python exec: :w  or :!",
                persistent=True,
                codefunc=py_code,
            )
            return CLAIM

        if not pycode:
            return self._py_console(caller, noecho="noecho" in switches)

        from .python_console import run_code_snippet

        run_code_snippet(
            caller,
            pycode,
            measure_time="time" in switches,
            client_raw="clientraw" in switches,
        )
        return CLAIM

    @staticmethod
    def _py_console(caller, noecho=False):
        """The interactive Python console as a generator the engine drives.

        Each ``yield prompt`` suspends the dispatch on player input, matching
        the stock ``CmdPy`` console loop line for line.
        """
        import sys

        from .python_console import EvenniaPythonConsole

        console = EvenniaPythonConsole(caller)
        banner = (
            "|gEvennia Interactive Python mode{echomode}\nPython {version} on {platform}".format(
                echomode=" (no echoing of prompts)" if noecho else "",
                version=sys.version,
                platform=sys.platform,
            )
        )
        caller.msg(banner)
        line = ""
        main_prompt = "|x[py mode - quit() to exit]|n"
        prompt = main_prompt
        while line.lower() not in ("exit", "exit()"):
            try:
                line = yield (prompt)
                if noecho:
                    prompt = "..." if console.push(line) else main_prompt
                else:
                    if line:
                        caller.msg(f">>> {line}")
                    prompt = line if console.push(line) else main_prompt
            except SystemExit:
                break
        caller.msg("|gClosing the Python console.|n")
        return CLAIM


class CharacterSystemRules(PyRules):
    """Baseline character-side system rules: ``@systems``, ``@tasks``, ``@py``."""

    def _is_actor(self, actor) -> bool:
        return self is getattr(actor, "character", None)

    # --- @objects ----------------------------------------------------------------

    @rule(
        Objects,
        phase="carry_out",
        requires=HasCapability("engine.system.inspect", principal_scope="account"),
    )
    def carry_out_objects(self, action, actor):
        """Render database object totals and the latest objects."""
        if not self._is_actor(actor):
            return SKIP
        from evennia.objects.models import ObjectDB
        from evennia.utils.evtable import EvTable
        from evennia.utils.utils import class_from_module, datetime_format

        caller = self
        limit = int(action.args) if action.args and action.args.isdigit() else 10
        total = ObjectDB.objects.count()
        character = class_from_module(settings.BASE_CHARACTER_TYPECLASS)
        room = class_from_module(settings.BASE_ROOM_TYPECLASS)
        exit_type = class_from_module(settings.BASE_EXIT_TYPECLASS)
        characters = character.objects.all_family().count()
        rooms = room.objects.all_family().count()
        exits = exit_type.objects.all_family().count()
        other = total - characters - rooms - exits
        denominator = total or 1

        totals = EvTable("|wtype|n", "|wcomment|n", "|wcount|n", "|w%|n", border="table", align="l")
        for label, comment, count in (
            ("Characters", "(BASE_CHARACTER_TYPECLASS + children)", characters),
            ("Rooms", "(BASE_ROOM_TYPECLASS + children)", rooms),
            ("Exits", "(BASE_EXIT_TYPECLASS + children)", exits),
            ("Other", "", other),
        ):
            totals.add_row(label, comment, count, f"{(float(count) / denominator) * 100:.2f}")

        typeclasses = EvTable("|wtypeclass|n", "|wcount|n", "|w%|n", border="table", align="l")
        for stat in ObjectDB.objects.get_typeclass_totals():
            typeclasses.add_row(
                stat.get("typeclass", "<error>"),
                stat.get("count", -1),
                f"{stat.get('percent', -1):.2f}",
            )

        latest = EvTable(
            "|wcreated|n", "|wdbref|n", "|wname|n", "|wtypeclass|n", align="l", border="table"
        )
        objects = ObjectDB.objects.all().order_by("db_date_created")[max(0, total - limit) :]
        for obj in objects:
            latest.add_row(datetime_format(obj.date_created), obj.dbref, obj.key, obj.path)
        caller.msg(
            f"\n|wObject subtype totals (out of {total} Objects):|n\n{totals}"
            f"\n|wObject typeclass distribution:|n\n{typeclasses}"
            f"\n|wLast {min(total, limit)} Objects created:|n\n{latest}"
        )
        return CLAIM

    # --- @scripts ----------------------------------------------------------------

    @staticmethod
    def _script_parts(action):
        """Return ``(object, key, typeclass)`` query parts from mux arguments."""

        def separate(part):
            first, *rest = part.split(":", 1)
            return (first, rest[0]) if rest else (None, first)

        if action.rhs:
            key, typeclass = separate(action.rhs)
            return action.lhs, key, typeclass
        if action.rhs is not None:
            return action.lhs, None, None
        key, typeclass = separate(action.args)
        return None, key, typeclass

    @staticmethod
    def _search_scripts(key, typeclass):
        """Find storage scripts by dbref, exact key/path, path suffix, or range."""
        from evennia.scripts.models import ScriptDB
        from evennia.utils.utils import dbref

        hidden = ("evennia.prototypes.prototypes.DbPrototype",)
        script_id = dbref(typeclass)
        if script_id:
            return ScriptDB.objects.get_all_scripts(typeclass)
        if key:
            return ScriptDB.objects.filter(
                db_key__iexact=key, db_typeclass_path__iendswith=typeclass
            ).exclude(db_typeclass_path__in=hidden)
        scripts = (
            ScriptDB.objects.filter(db_typeclass_path__iendswith=typeclass)
            .exclude(db_typeclass_path__in=hidden)
            .order_by("id")
        )
        if scripts:
            return scripts
        if "-" in typeclass:
            try:
                start, end = (dbref(part.strip()) for part in typeclass.split("-", 1))
            except (TypeError, ValueError):
                start = end = None
            if start and end:
                return (
                    ScriptDB.objects.filter(id__in=range(start, end + 1))
                    .exclude(db_typeclass_path__in=hidden)
                    .order_by("id")
                )
        return scripts

    def _scripts_flow(self, action, actor):
        """Generator implementing storage-script lookup and multi-delete confirmation."""
        from evennia.scripts.models import ScriptDB
        from evennia.utils import create, logger

        caller = self
        hidden = ("evennia.prototypes.prototypes.DbPrototype",)
        session = getattr(actor, "session", None)
        if not action.args:
            scripts = ScriptDB.objects.all().exclude(db_typeclass_path__in=hidden)
            if not scripts:
                caller.msg("No scripts found.")
            else:
                _show_scripts(caller, scripts.order_by("id"), session=session)
            return CLAIM

        obj_query, key_query, typeclass_query = self._script_parts(action)
        obj = caller.search(obj_query, global_search=True) if obj_query else None
        scripts = self._search_scripts(key_query, typeclass_query) if typeclass_query else None

        if not action.switches:
            if obj:
                if action.rhs:
                    if not (obj.access(caller, "control") or obj.access(caller, "edit")):
                        caller.msg(f"You don't have permission to edit {obj.key}.")
                        return CLAIM
                    created = obj.scripts.add(typeclass_query, key=key_query)
                    caller.msg(
                        f"Script |w{action.rhs}|n successfully added to {obj.get_display_name(caller)}."
                        if created
                        else f"Script {action.rhs} could not be added to {obj.get_display_name(caller)}."
                    )
                else:
                    attached = ScriptDB.objects.filter(db_obj=obj).exclude(
                        db_typeclass_path__in=hidden
                    )
                    if attached:
                        _show_scripts(caller, attached.order_by("id"), session=session)
                    else:
                        caller.msg(f"No scripts defined on {obj}")
                return CLAIM
            if scripts:
                _show_scripts(caller, scripts.order_by("id"), session=session)
                return CLAIM
            from evennia.utils.utils import dbref

            if dbref(typeclass_query):
                caller.msg(f"No script found with dbref {typeclass_query}")
                return CLAIM
            try:
                created = create.create_script(typeclass=typeclass_query, key=key_query)
            except ImportError:
                logger.log_trace()
                created = None
            if created:
                caller.msg(f"Global Script Created - {created.key} ({created.typeclass_path})")
                _show_scripts(caller, [created], session=session)
            else:
                caller.msg(
                    f"Global Script |rNOT|n Created |r(see log)|n - arguments: {action.args}"
                )
            return CLAIM

        if "delete" not in action.switches:
            caller.msg("Usage: @scripts[/delete] [script or object = script]")
            return CLAIM
        if obj:
            if not (obj.access(caller, "control") or obj.access(caller, "edit")):
                caller.msg(f"You don't have permission to edit {obj.key}.")
                return CLAIM
            from django.db.models import Q

            from evennia.utils.utils import dbref

            scripts = ScriptDB.objects.filter(db_obj=obj).exclude(db_typeclass_path__in=hidden)
            if key_query:
                scripts = scripts.filter(
                    db_key__iexact=key_query,
                    db_typeclass_path__iendswith=typeclass_query,
                )
            elif typeclass_query and dbref(typeclass_query):
                scripts = scripts.filter(id=dbref(typeclass_query))
            elif typeclass_query:
                scripts = scripts.filter(
                    Q(db_key__iexact=typeclass_query)
                    | Q(db_typeclass_path__iendswith=typeclass_query)
                )
        if not scripts:
            caller.msg("No scripts found.")
            return CLAIM
        count = scripts.count() if hasattr(scripts, "count") else len(scripts)
        if count > 1:
            reply = yield (
                f"Multiple scripts found: {scripts}. Are you sure you want to operate on all of them? [Y]/N? "
            )
            if (reply or "").lower() in ("n", "no"):
                caller.msg("Aborted.")
                return CLAIM
        messages = []
        for script in scripts:
            script_key = script.key
            typeclass_path = script.typeclass_path
            script_type = f"Script on {obj}" if obj else "Global Script"
            try:
                script.delete()
            except Exception:  # noqa: BLE001 - preserve batch progress and report
                logger.log_trace()
                messages.append(
                    f"{script_type} |rNOT|n |rDeleted|n |r(see log)|n - "
                    f"{script_key} ({typeclass_path})|n"
                )
            else:
                messages.append(f"{script_type} |rDeleted|n - {script_key} ({typeclass_path})")
        caller.msg("\n".join(messages))
        return CLAIM

    @rule(
        Scripts,
        phase="carry_out",
        requires=HasCapability("engine.script.control", principal_scope="account"),
    )
    def carry_out_scripts(self, action, actor):
        """Run native storage-script management (no timer controls)."""
        if not self._is_actor(actor):
            return SKIP
        return self._scripts_flow(action, actor)

    # --- @systems ----------------------------------------------------------------

    @rule(Systems, phase="carry_out", requires=HasCapability("engine.system.inspect"))
    def carry_out_systems(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.utils import systems
        from evennia.utils.evtable import EvTable
        from evennia.utils.utils import datetime_format

        caller = self
        registered = systems.all_systems()
        if not registered:
            caller.msg("No systems are registered with the scheduler.")
            return CLAIM
        # Keep each wire message bounded. A real game commonly registers dozens
        # of systems, and one monolithic EvTable grows beyond the safe envelope
        # expected by structured web clients. Isolate each row too: introspection
        # must remain available when one third-party System has malformed display
        # metadata.
        page_size = 10
        page_count = (len(registered) + page_size - 1) // page_size
        for page_index in range(page_count):
            table = EvTable("system", "cadence", "scope", "last fired", "fires", "in flight")
            start = page_index * page_size
            for system in registered[start : start + page_size]:
                try:
                    last_fired = (
                        datetime_format(datetime.datetime.fromtimestamp(float(system.last_run)))
                        if system.last_run
                        else "-"
                    )
                    row = (
                        str(system.name)[:32],
                        str(system.cadence.describe())[:32],
                        str(system.scope.describe())[:64],
                        last_fired,
                        system.fire_count,
                        "*" if system.in_flight else "-",
                    )
                except Exception:
                    from evennia.utils import logger

                    logger.log_trace(
                        f"@systems could not render scheduler entry "
                        f"{getattr(system, 'name', '<unknown>')!r}"
                    )
                    row = (
                        str(getattr(system, "name", "<unknown>"))[:32],
                        "<error>",
                        "<error>",
                        "-",
                        "?",
                        "?",
                    )
                table.add_row(*row)
            caller.msg(f"|wRegistered systems|n |x({page_index + 1}/{page_count})|n:\n{table}")
        return CLAIM

    # --- @tasks ------------------------------------------------------------------

    @rule(Tasks, phase="carry_out", requires=HasCapability("engine.runtime.manage"))
    def carry_out_tasks(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.scripts import taskhandler
        from evennia.utils.evtable import EvTable
        from evennia.utils.utils import iter_to_str

        caller = self
        task_handler = taskhandler.TASK_HANDLER
        switches = action.switches

        if not task_handler.tasks:
            caller.msg("There are no active tasks.")
            if switches or action.args:
                caller.msg("Likely the task has completed and been removed.")
            return CLAIM

        # a request to manipulate task(s)
        if switches and action.lhs:
            action_request = switches[0]
            try:
                arg_is_id = int(action.lhslist[0])
            except ValueError:
                arg_is_id = False

            if arg_is_id:
                task_comp_msg = "Task completed while processing request."
                task_id = arg_is_id
                task = taskhandler.TaskHandlerTask(task_id)

                if not task.exists():
                    caller.msg(f"Task {task_id} does not exist.")
                    return CLAIM

                switch_action = getattr(task, action_request, False)
                if not switch_action:
                    caller.msg(
                        f"{switches[0]}, is not an acceptable task action or "
                        f"{task_comp_msg.lower()}"
                    )

                t_comp_date = t_func_mem_ref = t_func_name = None
                if task_id in task_handler.tasks:
                    task_args = task_handler.tasks.get(task_id, False)
                    if not task_args:
                        caller.msg(task_comp_msg)
                        return CLAIM
                    t_comp_date, t_func_mem_ref = _coll_date_func(task_args)
                    t_func_name = str(task_args[1]).split(" ")
                    t_func_name = t_func_name[1] if len(t_func_name) >= 2 else None

                if task.exists():
                    prompt = (
                        f"{action_request.capitalize()} task {task_id} with completion date "
                        f"{t_comp_date} ({t_func_name}) {{options}}?"
                    )
                    no_msg = f"No {action_request} processed."
                    ask_yes_no(
                        caller,
                        prompt=prompt,
                        yes_action=_make_task_action(
                            caller,
                            task_id,
                            t_comp_date,
                            t_func_mem_ref,
                            switch_action,
                            action_request,
                        ),
                        no_action=no_msg,
                        default="Y",
                        allow_abort=True,
                    )
                    return CLAIM
                caller.msg(task_comp_msg)
                return CLAIM

            # by function name: act on every task deferring that function
            name_match_found = False
            arg_func_name = action.lhslist[0].lower()

            current_tasks = dict(task_handler.tasks)
            for task_id, task_args in current_tasks.items():
                t_func_name = str(task_args[1]).split(" ")
                t_func_name = t_func_name[1] if len(t_func_name) >= 2 else None
                if arg_func_name != t_func_name:
                    continue
                name_match_found = True
                task = taskhandler.TaskHandlerTask(task_id)
                switch_action = getattr(task, action_request, False)
                if switch_action:
                    action_return = switch_action()
                    caller.msg(f"Task action {action_request} completed on task ID {task_id}.")
                    caller.msg(f"The task function {action_request} returned: {action_return}")

            if not name_match_found:
                caller.msg(f"No tasks deferring function name {arg_func_name} found.")
            return CLAIM

        if switches or action.lhs:
            caller.msg("Task command misformed.")
            caller.msg("Proper format tasks[/switch] [function name or task id]")
            return CLAIM

        # no manipulation requested: list all tasks
        session = getattr(actor, "session", None)
        width = settings.CLIENT_DEFAULT_WIDTH
        if session is not None and hasattr(session, "get_client_size"):
            client_width, _ = session.get_client_size()
            width = client_width or width

        tasks_header = (
            "Task ID",
            "Completion Date",
            "Function",
            "Arguments",
            "KWARGS",
            "persistent",
        )
        tasks_list = [list() for _ in range(len(tasks_header))]
        for task_id, task in task_handler.tasks.items():
            t_comp_date, _ = _coll_date_func(task)
            t_func_name = str(task[1]).split(" ")
            t_func_name = t_func_name[1] if len(t_func_name) >= 2 else None
            t_args = str(task[2])
            t_kwargs = str(task[3])
            t_pers = str(task[4])
            task_data = (task_id, t_comp_date, t_func_name, t_args, t_kwargs, t_pers)
            for i in range(len(tasks_header)):
                tasks_list[i].append(task_data[i])
        tasks_table = EvTable(
            *tasks_header, table=tasks_list, maxwidth=width, border="cells", align="c"
        )
        actions = (
            f"/{switch}" for switch in ("pause", "unpause", "do_task", "call", "remove", "cancel")
        )
        helptxt = f"\nActions: {iter_to_str(actions)}"
        caller.msg(str(tasks_table) + helptxt)
        return CLAIM


def __getattr__(name):
    """Lazily expose the pager to avoid the EvMore/action import cycle."""
    if name == "ScriptEvMore":
        from .script_paging import ScriptEvMore

        return ScriptEvMore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
