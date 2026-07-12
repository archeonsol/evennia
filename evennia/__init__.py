"""
Evennia MUD/MUX/MU* creation system

This is the main top-level API for Evennia. You can explore the evennia library
by accessing evennia.<subpackage> directly. From inside the game you can read
docs of all object by viewing its `__doc__` string, such as through

    py evennia.ObjectDB.__doc__

For full functionality you should explore this module via a django-
aware shell. Go to your game directory and use the command

   evennia shell

to launch such a shell (using python or ipython depending on your install).
See www.evennia.com for full documentation.

"""

import importlib

# docstring header

DOCSTRING = """
Evennia MU* creation system.

Online manual and API docs are found at http://www.evennia.com.

Flat-API shortcut names:
{}
"""

# ---------------------------------------------------------------------------
# Lazy export registry.
#
# Each entry maps a public attribute on the `evennia` module to a
# `"module:attr"` spec. The module path may be relative (leading ".") and is
# resolved via `importlib.import_module(path, __name__)`. When the attr part
# is empty (e.g. `".server.signals"`), the imported module itself is bound.
#
# Resolution happens lazily on first attribute access via PEP 562
# `__getattr__`. Each resolved value is cached back into module globals, so
# subsequent access is a plain dict lookup with no overhead.
#
# Entries that cannot be expressed as pure imports (dynamic instances built in
# `_init`, or values populated by the portal/server boot gating) are not in
# the registry; they are set directly by `_init` and live alongside the lazy
# entries in `__all__`.
# ---------------------------------------------------------------------------

_LAZY_EXPORTS = {
    # Typeclasses
    "DefaultAccount": ".accounts.accounts:DefaultAccount",
    "DefaultGuest": ".accounts.accounts:DefaultGuest",
    "DefaultObject": ".objects.objects:DefaultObject",
    "DefaultCharacter": ".objects.objects:DefaultCharacter",
    "DefaultRoom": ".objects.objects:DefaultRoom",
    "DefaultExit": ".objects.objects:DefaultExit",
    "DefaultChannel": ".comms.comms:DefaultChannel",
    "DefaultScript": ".scripts.scripts:DefaultScript",
    # Database models
    "ObjectDB": ".objects.models:ObjectDB",
    "AccountDB": ".accounts.models:AccountDB",
    "ScriptDB": ".scripts.models:ScriptDB",
    "ChannelDB": ".comms.models:ChannelDB",
    "Msg": ".comms.models:Msg",
    "ServerConfig": ".server.models:ServerConfig",
    # Properties
    "AttributeProperty": ".typeclasses.attributes:AttributeProperty",
    "TagProperty": ".typeclasses.tags:TagProperty",
    "TagCategoryProperty": ".typeclasses.tags:TagCategoryProperty",
    # commands
    "Command": ".commands.command:Command",
    "CmdSet": ".commands.cmdset:CmdSet",
    "InterruptCommand": ".commands.command:InterruptCommand",
    # search functions
    "search_object": ".utils.search:search_object",
    "search_script": ".utils.search:search_script",
    "search_account": ".utils.search:search_account",
    "search_channel": ".utils.search:search_channel",
    "search_message": ".utils.search:search_message",
    "search_help": ".utils.search:search_help",
    "search_tag": ".utils.search:search_tag",
    # create functions
    "create_object": ".utils.create:create_object",
    "create_script": ".utils.create:create_script",
    "create_account": ".utils.create:create_account",
    "create_channel": ".utils.create:create_channel",
    "create_message": ".utils.create:create_message",
    "create_help_entry": ".utils.create:create_help_entry",
    # utilities (submodules surface as module objects)
    "settings": "django.conf:settings",
    "lockfuncs": ".locks:lockfuncs",
    "logger": ".utils.logger:",
    "gametime": ".utils.gametime:",
    "ansi": ".utils.ansi:",
    "spawn": ".prototypes.spawner:spawn",
    "contrib": ".contrib:",
    "EvTable": ".utils.evtable:EvTable",
    "EvForm": ".utils.evform:EvForm",
    "EvEditor": ".utils.eveditor:EvEditor",
    "EvMore": ".utils.evmore:EvMore",
    "ANSIString": ".utils.ansi:ANSIString",
    "signals": ".server.signals:",
    "hooks": ".hooks:",
    "authorization": ".authorization:",
    "FuncParser": ".utils.funcparser:FuncParser",
    "OnDemandTask": ".scripts.ondemandhandler:OnDemandTask",
    "standalone": ".standalone:standalone",
    "shutdown_standalone": ".standalone:shutdown_standalone",
    # Handlers (singletons exposed as module attributes)
    "TASK_HANDLER": ".scripts.taskhandler:TASK_HANDLER",
    "MONITOR_HANDLER": ".scripts.monitorhandler:MONITOR_HANDLER",
    "ON_DEMAND_HANDLER": ".scripts.ondemandhandler:ON_DEMAND_HANDLER",
}

# Names populated by `_init` rather than by lazy import (dynamic instances or
# values that depend on portal-vs-server boot mode). Each starts as `None` so
# pre-init access returns the historical sentinel value rather than raising.
_INIT_POPULATED = (
    "managers",
    "default_cmds",
    "syscmdkeys",
    "SESSION_HANDLER",
    "PORTAL_SESSION_HANDLER",
    "SERVER_SESSION_HANDLER",
    "GLOBAL_SCRIPTS",
    "OPTION_CLASSES",
    "PROCESS_ID",
    "TWISTED_APPLICATION",
    "EVENNIA_PORTAL_SERVICE",
    "EVENNIA_SERVER_SERVICE",
)

for _name in _INIT_POPULATED:
    globals()[_name] = None
del _name

PORTAL_MODE = False

__all__ = sorted(set(_LAZY_EXPORTS) | set(_INIT_POPULATED) | {"PORTAL_MODE"})


def __getattr__(name):
    """Lazily resolve a flat-API export on first attribute access.

    The registry is consulted only for names that aren't already bound on
    the module (Python's attribute lookup only falls through to
    `__getattr__` for missing names). After resolution the value is cached
    in module globals so subsequent access is a plain dict lookup.

    Raises:
        AttributeError: if `name` is not in the registry. This preserves the
            "module has no attribute" contract for unknown names.

    """
    try:
        spec = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, _, attr = spec.partition(":")
    module = importlib.import_module(module_path, __name__)
    value = getattr(module, attr) if attr else module
    globals()[name] = value
    return value


def _create_version():
    """
    Helper function for building the version string
    """
    import os
    from subprocess import STDOUT, CalledProcessError, check_output

    version = "Unknown"
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(root, "VERSION.txt"), "r") as f:
            version = f.read().strip()
    except IOError as err:
        print(err)
    try:
        rev = (
            check_output("git rev-parse --short HEAD", shell=True, cwd=root, stderr=STDOUT)
            .strip()
            .decode()
        )
        version = "%s (rev %s)" % (version, rev)
    except (IOError, CalledProcessError, OSError):
        # ignore if we cannot get to git
        pass
    return version


__version__ = _create_version()
del _create_version

_LOADED = False


def _init(portal_mode=False):
    """
    This function is called automatically by the launcher only after
    Evennia has fully initialized all its models. It sets up the API
    in a safe environment where all models are available already.

    The lazy `__getattr__` registry handles pure-import exports on first
    access; this function still runs to construct the dynamic container
    instances (`managers`, `default_cmds`, `syscmdkeys`) and to wire up
    the portal-vs-server boot state (session handlers, Twisted service).

    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    global managers, default_cmds, syscmdkeys
    global SESSION_HANDLER, PORTAL_SESSION_HANDLER, SERVER_SESSION_HANDLER
    global PROCESS_ID, TWISTED_APPLICATION
    global EVENNIA_PORTAL_SERVICE, EVENNIA_SERVER_SERVICE
    global GLOBAL_SCRIPTS, OPTION_CLASSES
    global PORTAL_MODE
    PORTAL_MODE = portal_mode

    import os

    from django.conf import settings

    from .utils.utils import class_from_module

    if not PORTAL_MODE:
        # containers (server-only)
        from .utils.containers import GLOBAL_SCRIPTS, OPTION_CLASSES

    PROCESS_ID = os.getpid()

    from evennia.server.service_registry import ServiceCollection

    TWISTED_APPLICATION = ServiceCollection("Evennia")

    if portal_mode:
        # Set up the PortalSessionHandler
        from evennia.server.portal import portalsessionhandler

        portal_sess_handler_class = class_from_module(settings.PORTAL_SESSION_HANDLER_CLASS)
        portalsessionhandler.PORTAL_SESSIONS = portal_sess_handler_class()
        SESSION_HANDLER = portalsessionhandler.PORTAL_SESSIONS
        PORTAL_SESSION_HANDLER = SESSION_HANDLER
        _evennia_service_class = class_from_module(settings.EVENNIA_PORTAL_SERVICE_CLASS)
        EVENNIA_PORTAL_SERVICE = _evennia_service_class()
        EVENNIA_PORTAL_SERVICE.setServiceParent(TWISTED_APPLICATION)

        from django.db import connection

        # we don't need a connection to the database so close it right away
        try:
            connection.close()
        except Exception:
            pass

    else:
        # Create the ServerSesssionHandler
        from evennia.server import sessionhandler

        sess_handler_class = class_from_module(settings.SERVER_SESSION_HANDLER_CLASS)
        sessionhandler.SESSIONS = sess_handler_class()
        sessionhandler.SESSION_HANDLER = sessionhandler.SESSIONS
        SESSION_HANDLER = sessionhandler.SESSIONS
        SERVER_SESSION_HANDLER = SESSION_HANDLER
        _evennia_service_class = class_from_module(settings.EVENNIA_SERVER_SERVICE_CLASS)
        EVENNIA_SERVER_SERVICE = _evennia_service_class()
        EVENNIA_SERVER_SERVICE.setServiceParent(TWISTED_APPLICATION)

    # API containers

    class _EvContainer(object):
        """
        Parent for other containers

        """

        def _help(self):
            "Returns list of contents"
            names = [name for name in self.__class__.__dict__ if not name.startswith("_")]
            names += [name for name in self.__dict__ if not name.startswith("_")]
            print(self.__doc__ + "-" * 60 + "\n" + ", ".join(names))

        help = property(_help)

    class DBmanagers(_EvContainer):
        """
        Links to instantiated Django database managers. These are used
        to perform more advanced custom database queries than the standard
        search functions allow.

        helpentries - HelpEntry.objects
        accounts - AccountDB.objects
        scripts - ScriptDB.objects
        msgs    - Msg.objects
        channels - Channel.objects
        objects - ObjectDB.objects
        serverconfigs - ServerConfig.objects
        tags - Tags.objects

        """

        from .accounts.models import AccountDB
        from .comms.models import ChannelDB, Msg
        from .help.models import HelpEntry
        from .objects.models import ObjectDB
        from .scripts.models import ScriptDB
        from .server.models import ServerConfig
        from .typeclasses.tags import Tag

        # create container's properties
        helpentries = HelpEntry.objects
        accounts = AccountDB.objects
        scripts = ScriptDB.objects
        msgs = Msg.objects
        channels = ChannelDB.objects
        objects = ObjectDB.objects
        serverconfigs = ServerConfig.objects
        tags = Tag.objects
        # remove these so they are not visible as properties
        del HelpEntry, AccountDB, ScriptDB, Msg, ChannelDB
        # del ExternalChannelConnection
        del ObjectDB, ServerConfig, Tag

    managers = DBmanagers()
    del DBmanagers

    class DefaultCmds(_EvContainer):
        """
        This container holds direct shortcuts to all default commands in Evennia.

        To access in code, do 'from evennia import default_cmds' then
        access the properties on the imported default_cmds object.

        """

        from .commands.command import AccountCommand, Command
        from .commands.default.cmdset_account import AccountCmdSet
        from .commands.default.cmdset_character import CharacterCmdSet
        from .commands.default.cmdset_session import SessionCmdSet
        from .commands.default.cmdset_unloggedin import UnloggedinCmdSet

        def __init__(self):
            "populate the object with commands"

            def add_cmds(module):
                "helper method for populating this object with cmds"
                from evennia.utils import utils

                cmdlist = utils.variable_from_module(module, module.__all__)
                self.__dict__.update(dict([(c.__name__, c) for c in cmdlist]))

            from .commands.default import (
                account,
                admin,
                building,
                comms,
                general,
                help,
                system,
                unloggedin,
            )

            add_cmds(admin)
            add_cmds(building)
            add_cmds(comms)
            add_cmds(general)
            add_cmds(account)
            add_cmds(help)
            add_cmds(system)
            add_cmds(unloggedin)

    default_cmds = DefaultCmds()
    del DefaultCmds

    class SystemCmds(_EvContainer):
        """
        Creating commands with keys set to these constants will make
        them system commands called as a replacement by the parser when
        special situations occur. If not defined, the hard-coded
        responses in the server are used.

        CMD_NOINPUT - no input was given on command line
        CMD_NOMATCH - no valid command key was found
        CMD_MULTIMATCH - multiple command matches were found
        CMD_LOGINSTART - this command will be called as the very
                         first command when an account connects to
                         the server.

        To access in code, do 'from evennia import syscmdkeys' then
        access the properties on the imported syscmdkeys object.

        """

        from .commands import cmdhandler

        CMD_NOINPUT = cmdhandler.CMD_NOINPUT
        CMD_NOMATCH = cmdhandler.CMD_NOMATCH
        CMD_MULTIMATCH = cmdhandler.CMD_MULTIMATCH
        CMD_LOGINSTART = cmdhandler.CMD_LOGINSTART
        del cmdhandler

    syscmdkeys = SystemCmds()
    del SystemCmds
    del _EvContainer


def set_trace(term_size=(140, 80), debugger="auto"):
    """
    Helper function for running a debugger inside the Evennia event loop.

    Args:
        term_size (tuple, optional): Only used for Pudb and defines the size of the terminal
            (width, height) in number of characters.
        debugger (str, optional): One of 'auto', 'pdb' or 'pudb'. Pdb is the standard debugger. Pudb
            is an external package with a different, more 'graphical', ncurses-based UI. With
            'auto', will use pudb if possible, otherwise fall back to pdb. Pudb is available through
            `pip install pudb`.

    Notes:
        To use:

        1) add this to a line to act as a breakpoint for entering the debugger:

            from evennia import set_trace; set_trace()

        2) restart evennia in interactive mode

            evennia istart

        3) debugger will appear in the interactive terminal when breakpoint is reached. Exit
           with 'q', remove the break line and restart server when finished.

    """
    import sys

    dbg = None

    if debugger in ("auto", "pudb"):
        try:
            from pudb import debugger

            dbg = debugger.Debugger(stdout=sys.__stdout__, term_size=term_size)
        except ImportError:
            if debugger == "pudb":
                raise
            pass

    if not dbg:
        import pdb

        dbg = pdb.Pdb(stdout=sys.__stdout__)

    try:
        # Start debugger, forcing it up one stack frame (otherwise `set_trace`
        # will start debugger this point, not the actual code location)
        dbg.set_trace(sys._getframe().f_back)
    except Exception:
        # Stopped at breakpoint. Press 'n' to continue into the code.
        dbg.set_trace()


# initialize the doc string from the declared public surface
__doc__ = DOCSTRING.format("\n- " + "\n- ".join(f"evennia.{key}" for key in __all__))
