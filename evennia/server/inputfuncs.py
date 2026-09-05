"""
Functions for processing input commands.

All global functions in this module whose name does not start with "_"
is considered an inputfunc. Each function must have the following
callsign (where inputfunc name is always lower-case, no matter what the
OOB input name looked like):

    inputfunc(session, *args, **kwargs)

Where "options" is always one of the kwargs, containing eventual
protocol-options.
There is one special function, the "default" function, which is called
on a no-match. It has this callsign:

    default(session, cmdname, *args, **kwargs)

Evennia knows which modules to use for inputfuncs by
settings.INPUT_FUNC_MODULES.

"""

import importlib
from codecs import lookup as codecs_lookup

from django.conf import settings

from evennia.accounts.models import AccountDB
from evennia.commands.cmdhandler import cmdhandler
from evennia.utils.logger import log_err, log_warn
from evennia.utils.utils import to_str

BrowserSessionStore = importlib.import_module(settings.SESSION_ENGINE).SessionStore


_GA = object.__getattribute__
_SA = object.__setattr__


def _idle_commands():
    """Tuple of strings that count as the idle command.

    Always includes "idle" because the webclient uses it; if the
    configured IDLE_COMMAND is something other than "idle", both
    are recognized.
    """
    cmd = settings.IDLE_COMMAND
    return (cmd,) if cmd == "idle" else (cmd, "idle")


_STRIP_MXP = None


def _NA(o):
    return "N/A"


def _maybe_strip_incoming_mxp(txt):
    global _STRIP_MXP
    if settings.MXP_ENABLED and settings.MXP_OUTGOING_ONLY:
        if not _STRIP_MXP:
            from evennia.utils.ansi import strip_mxp as _STRIP_MXP
        return _STRIP_MXP(txt)
    return txt


_ERROR_INPUT = "Inputfunc {name}({session}): Wrong/unrecognized input: {inp}"


# All global functions are inputfuncs available to process inputs


def text(session, *args, **kwargs):
    """
    Main text input from the client. This will execute a command
    string on the server.

    Args:
        session (Session): The active Session to receive the input.
        text (str): First arg is used as text-command input. Other
            arguments are ignored.

    """

    # from evennia.server.profiling.timetrace import timetrace
    # text = timetrace(text, "ServerSession.data_in")

    txt = args[0] if args else None

    # explicitly check for None since text can be an empty string, which is
    # also valid
    if txt is None:
        return
    # this is treated as a command input
    # handle the 'idle' command
    if txt.strip() in _idle_commands():
        session.update_session_counters(idle=True)
        return

    txt = _maybe_strip_incoming_mxp(txt)

    if session.account:
        # nick replacement
        puppet = session.get_puppet()
        if puppet:
            txt = puppet.nicks.nickreplace(txt, categories=("inputline"), include_account=True)
        else:
            txt = session.account.nicks.nickreplace(
                txt, categories=("inputline"), include_account=False
            )
    kwargs.pop("options", None)
    from evennia.utils import clock

    # cmdhandler is `async def`; kick it off on the loop (unhandled errors
    # are logged via clock.run_coroutine's task done-callback).
    clock.run_coroutine(
        cmdhandler(session, txt, callertype="session", session=session, **kwargs),
        task_kind="command",
    )
    session.update_session_counters()


def bot_data_in(session, *args, **kwargs):
    """
    Text input from the IRC and RSS bots.
    This will trigger the execute_cmd method on the bots in-game counterpart.

    Args:
        session (Session): The active Session to receive the input.
        text (str): First arg is text input. Other arguments are ignored.

    """

    txt = args[0] if args else None

    # Explicitly check for None since text can be an empty string, which is
    # also valid
    if txt is None:
        return
    # this is treated as a command input
    # handle the 'idle' command
    if txt.strip() in _idle_commands():
        session.update_session_counters(idle=True)
        return

    txt = _maybe_strip_incoming_mxp(txt)

    kwargs.pop("options", None)
    account = session.account
    if account is None:
        # Bot transports may deliver one last frame while the Server is still
        # reconciling or tearing down the Portal session. There is no in-game
        # recipient in that state, so discard the frame and diagnose it once
        # per session instead of raising on every frame.
        if not getattr(session, "_bot_data_unbound_logged", False):
            log_warn(
                "bot_data_in: dropping input for unbound session "
                f"sessid={getattr(session, 'sessid', None)} "
                f"protocol={getattr(session, 'protocol_key', None)}"
            )
            session._bot_data_unbound_logged = True
        return
    # Trigger the execute_cmd method of the corresponding bot.
    account.execute_cmd(session=session, txt=txt, **kwargs)
    session.update_session_counters()


def echo(session, *args, **kwargs):
    """
    Echo test function
    """
    if settings.MXP_ENABLED and settings.MXP_OUTGOING_ONLY:
        args = [_maybe_strip_incoming_mxp(str(arg)) for arg in args]

    session.data_out(text=f"Echo returns: {args}, {kwargs}")


def default(session, cmdname, *args, **kwargs):
    """
    Default catch-function. This is like all other input functions except
    it will get `cmdname` as the first argument.

    """
    err = (
        "Session {sessid}: Input command not recognized:\n"
        " name: '{cmdname}'\n"
        " args, kwargs: {args}, {kwargs}".format(
            sessid=session.sessid, cmdname=cmdname, args=args, kwargs=kwargs
        )
    )
    if session.protocol_flags.get("INPUTDEBUG", False):
        session.msg(err)
    log_err(err)


_CLIENT_OPTIONS = (
    "ANSI",
    "XTERM256",
    "MXP",
    "UTF-8",
    "SCREENREADER",
    "ENCODING",
    "MCCP",
    "SCREENHEIGHT",
    "SCREENWIDTH",
    "AUTORESIZE",
    "INPUTDEBUG",
    "RAW",
    "NOCOLOR",
    "NOGOAHEAD",
    "LOCALECHO",
)


def client_options(session, *args, **kwargs):
    """
    This allows the client an OOB way to inform us about its name and capabilities.
    This will be integrated into the session settings

    Keyword Args:
        get (bool): If this is true, return the settings as a dict
            (ignore all other kwargs).
        client (str): A client identifier, like "mushclient".
        version (str): A client version
        ansi (bool): Supports ansi colors
        xterm256 (bool): Supports xterm256 colors or not
        mxp (bool): Supports MXP or not
        utf-8 (bool): Supports UTF-8 or not
        screenreader (bool): Screen-reader mode on/off
        mccp (bool): MCCP compression on/off
        screenheight (int): Screen height in lines
        screenwidth (int): Screen width in characters
        autoresize (bool): Use NAWS updates to dynamically adjust format
        inputdebug (bool): Debug input functions
        nocolor (bool): Strip color
        raw (bool): Turn off parsing
        localecho (bool): Turn on server-side echo (for clients not supporting it)

    """
    old_flags = session.protocol_flags
    if not kwargs or kwargs.get("get", False):
        # return current settings
        options = dict((key, old_flags[key]) for key in old_flags if key.upper() in _CLIENT_OPTIONS)
        session.msg(client_options=options)
        return

    def validate_encoding(val):
        # helper: change encoding
        try:
            codecs_lookup(val)
        except LookupError:
            raise RuntimeError("The encoding '|w%s|n' is invalid. " % val)
        return val

    def validate_size(val):
        return {0: int(val)}

    def validate_bool(val):
        if isinstance(val, str):
            return True if val.lower() in ("true", "on", "1") else False
        return bool(val)

    flags = {}
    for key, value in kwargs.items():
        key = key.lower()
        if key == "client":
            flags["CLIENTNAME"] = to_str(value)
        elif key == "version":
            if "CLIENTNAME" in flags:
                flags["CLIENTNAME"] = "%s %s" % (flags["CLIENTNAME"], to_str(value))
        elif key == "ENCODING":
            flags["ENCODING"] = validate_encoding(value)
        elif key == "ansi":
            flags["ANSI"] = validate_bool(value)
        elif key == "xterm256":
            flags["XTERM256"] = validate_bool(value)
        elif key == "mxp":
            flags["MXP"] = validate_bool(value)
        elif key == "utf-8":
            flags["UTF-8"] = validate_bool(value)
        elif key == "screenreader":
            flags["SCREENREADER"] = validate_bool(value)
        elif key == "mccp":
            flags["MCCP"] = validate_bool(value)
        elif key == "screenheight":
            flags["SCREENHEIGHT"] = validate_size(value)
        elif key == "screenwidth":
            flags["SCREENWIDTH"] = validate_size(value)
        elif key == "autoresize":
            flags["AUTORESIZE"] = validate_bool(value)
        elif key == "inputdebug":
            flags["INPUTDEBUG"] = validate_bool(value)
        elif key == "nocolor":
            flags["NOCOLOR"] = validate_bool(value)
        elif key == "raw":
            flags["RAW"] = validate_bool(value)
        elif key == "nogoahead":
            flags["NOGOAHEAD"] = validate_bool(value)
        elif key == "localecho":
            flags["LOCALECHO"] = validate_bool(value)
        elif key in (
            "Char 1",
            "Char.Skills 1",
            "Char.Items 1",
            "Room 1",
            "IRE.Rift 1",
            "IRE.Composer 1",
        ):
            # ignore mudlet's default send (aimed at IRE games)
            pass
        elif key not in ("options", "cmdid"):
            err = _ERROR_INPUT.format(name="client_settings", session=session, inp=key)
            session.msg(text=err)

    session.protocol_flags.update(flags)
    # we must update the protocol flags on the portal session copy as well
    session.sessionhandler.session_portal_partial_sync({session.sessid: {"protocol_flags": flags}})


def get_client_options(session, *args, **kwargs):
    """
    Alias wrapper for getting options.
    """
    client_options(session, get=True)


def get_inputfuncs(session, *args, **kwargs):
    """
    Get the keys of all available inputfuncs. Note that we don't get
    it from this module alone since multiple modules could be added.
    So we get it from the sessionhandler.
    """
    inputfuncsdict = dict(
        (key, func.__doc__) for key, func in session.sessionhandler.get_inputfuncs().items()
    )
    session.msg(get_inputfuncs=inputfuncsdict)


def login(session, *args, **kwargs):
    """
    Peform a login. This only works if session is currently not logged
    in. This will also automatically throttle too quick attempts.

    Keyword Args:
        name (str): Account name
        password (str): Plain-text password

    """
    if not session.logged_in and "name" in kwargs and "password" in kwargs:
        from evennia.actions.default.unloggedin import login_session

        login_session(session, kwargs["name"], kwargs["password"])


_gettable = {
    "name": lambda obj: obj.key,
    "key": lambda obj: obj.key,
    "location": lambda obj: obj.location.key if obj.location else "None",
    "servername": lambda obj: settings.SERVERNAME,
}


def get_value(session, *args, **kwargs):
    """
    Return the value of a given attribute or db_property on the
    session's current account or character.

    Keyword Args:
      name (str): Name of info value to return. Only names
        in the _gettable dictionary earlier in this module
        are accepted.

    """
    name = kwargs.get("name", "")
    obj = session.get_puppet() or session.account
    if name in _gettable:
        session.msg(get_value={"name": name, "value": _gettable[name](obj)})


_monitorable = {"name": "db_key", "location": "db_location", "desc": "desc"}


def _on_monitor_change(**kwargs):
    fieldname = kwargs["fieldname"]
    obj = kwargs["obj"]
    name = kwargs["name"]
    session = kwargs["session"]
    outputfunc_name = kwargs["outputfunc_name"]
    category = None

    # Attributes stored in the MonitorHandler with categories are
    # stored as fieldname "db_value[category_name]", but we need to
    # separate [category_name] because the actual attribute is stored on
    # the object as "db_value" with a separate "category" field.
    if hasattr(obj, "db_category") and obj.db_category != None:
        category = obj.db_category
        fieldname = fieldname.replace("[{}]".format(obj.db_category), "")

    # the session may be None if the char quits and someone
    # else then edits the object

    if session:
        callsign = {
            outputfunc_name: {
                "name": name,
                **({"category": category} if category is not None else {}),
                "value": _GA(obj, fieldname),
            }
        }
        session.msg(**callsign)


def monitor(session, *args, **kwargs):
    """
    Adds monitoring to a given property or Attribute.

    Keyword Args:
      name (str): The name of the property or Attribute
        to report. No db_* prefix is needed. Only names
        in the _monitorable dict earlier in this module
        are accepted.
      stop (bool): Stop monitoring the above name.
      outputfunc_name (str, optional): Change the name of
        the outputfunc name. This is used e.g. by MSDP which
        has its own specific output format.

    """
    from evennia.scripts.monitorhandler import MONITOR_HANDLER

    name = kwargs.get("name", None)
    outputfunc_name = kwargs.get("outputfunc_name", "monitor")
    category = kwargs.get("category", None)
    _puppet = session.get_puppet()
    if name and name in _monitorable and _puppet:
        field_name = _monitorable[name]
        obj = _puppet
        if kwargs.get("stop", False):
            MONITOR_HANDLER.remove(obj, field_name, idstring=session.sessid)
        else:
            # the handler will add fieldname and obj to the kwargs automatically
            MONITOR_HANDLER.add(
                obj,
                field_name,
                _on_monitor_change,
                idstring=session.sessid,
                persistent=False,
                name=name,
                session=session,
                outputfunc_name=outputfunc_name,
                category=category,
            )


def unmonitor(session, *args, **kwargs):
    """
    Wrapper for turning off monitoring
    """
    kwargs["stop"] = True
    monitor(session, *args, **kwargs)


def monitored(session, *args, **kwargs):
    """
    Report on what is being monitored

    """
    import pickle

    from evennia.scripts.monitorhandler import MONITOR_HANDLER

    def _safe_pickle(value):
        try:
            pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
            return value
        except Exception:
            return str(value)

    obj = session.get_puppet()
    monitors = []
    for mon_obj, fieldname, idstring, persistent, monitor_kwargs in MONITOR_HANDLER.all(obj=obj):
        safe_kwargs = {key: _safe_pickle(val) for key, val in monitor_kwargs.items()}
        monitors.append((_safe_pickle(mon_obj), fieldname, idstring, persistent, safe_kwargs))
    session.msg(monitored=(monitors, {}))


def _on_webclient_options_change(**kwargs):
    """
    Called when the webclient options stored on the account changes.
    Inform the interested clients of this change.
    """
    session = kwargs["session"]
    obj = kwargs["obj"]
    fieldname = kwargs["fieldname"]
    clientoptions = _GA(obj, fieldname)

    # the session may be None if the char quits and someone
    # else then edits the object
    if session:
        session.msg(webclient_options=clientoptions)


def webclient_options(session, *args, **kwargs):
    """
    Handles retrieving and changing of options related to the webclient.

    If kwargs is empty (or contains just a "cmdid"), the saved options will be
    sent back to the session.
    A monitor handler will be created to inform the client of any future options
    that changes.

    If kwargs is not empty, the key/values stored in there will be persisted
    to the account object.

    Keyword Args:
        <option name>: an option to save
    """
    account = session.account

    clientoptions = account.db._saved_webclient_options
    if not clientoptions:
        # No saved options for this account, copy and save the default.
        account.db._saved_webclient_options = settings.WEBCLIENT_OPTIONS.copy()
        # Get the _SaverDict created by the database.
        clientoptions = account.db._saved_webclient_options

    # The webclient adds a cmdid to every kwargs, but we don't need it.
    try:
        del kwargs["cmdid"]
    except KeyError:
        pass

    if not kwargs:
        # No kwargs: we are getting the stored options
        # Convert clientoptions to regular dict for sending.
        session.msg(webclient_options=dict(clientoptions))

        # Create a monitor. If a monitor already exists then it will replace
        # the previous one since it would use the same idstring
        from evennia.scripts.monitorhandler import MONITOR_HANDLER

        MONITOR_HANDLER.add(
            account,
            "_saved_webclient_options",
            _on_webclient_options_change,
            idstring=session.sessid,
            persistent=False,
            session=session,
        )
    else:
        # kwargs provided: persist them to the account object.
        clientoptions.update(kwargs)


# OOB protocol-specific aliases and wrappers

# GMCP aliases
hello = client_options
supports_set = client_options


# MSDP aliases (some of the the generic MSDP commands defined in the MSDP spec are prefixed
# by msdp_ at the protocol level)
# See https://tintin.sourceforge.io/protocols/msdp/
# These load unconditionally as part of the standard MSDP protocol surface. No
# bundled client currently speaks MSDP, but the inputfuncs are cheap to define
# and are kept available for standard third-party clients that do, so they are
# not gated on current usage.


def msdp_list(session, *args, **kwargs):
    """
    MSDP LIST command

    """
    from evennia.scripts.monitorhandler import MONITOR_HANDLER

    args_lower = [arg.lower() for arg in args]
    if "commands" in args_lower:
        inputfuncs = [
            key[5:] if key.startswith("msdp_") else key
            for key in session.sessionhandler.get_inputfuncs().keys()
        ]
        session.msg(commands=(inputfuncs, {}))
    if "lists" in args_lower:
        session.msg(
            lists=(
                [
                    "commands",
                    "lists",
                    "configurable_variables",
                    "reportable_variables",
                    "reported_variables",
                    "sendable_variables",
                ],
                {},
            )
        )
    if "configurable_variables" in args_lower:
        session.msg(configurable_variables=(_CLIENT_OPTIONS, {}))
    if "reportable_variables" in args_lower:
        session.msg(reportable_variables=(_monitorable, {}))
    if "reported_variables" in args_lower:
        obj = session.get_puppet()
        monitor_infos = MONITOR_HANDLER.all(obj=obj)
        fieldnames = [tup[1] for tup in monitor_infos]
        session.msg(reported_variables=(fieldnames, {}))
    if "sendable_variables" in args_lower:
        session.msg(sendable_variables=(_monitorable, {}))


def msdp_report(session, *args, **kwargs):
    """
    MSDP REPORT command

    """
    kwargs["outputfunc_name"] = "report"
    monitor(session, *args, **kwargs)


def msdp_unreport(session, *args, **kwargs):
    """
    MSDP UNREPORT command

    """
    unmonitor(session, *args, **kwargs)


def msdp_send(session, *args, **kwargs):
    """
    MSDP SEND command
    """
    out = {}
    for varname in args:
        if varname.lower() in _monitorable:
            out[varname] = _monitorable[varname.lower()]
    session.msg(send=((), out))


# client specific


def _not_implemented(session, *args, **kwargs):
    """
    Dummy used to swallow missing-inputfunc errors for
    common clients.
    """
    pass


# GMCP External.Discord.Hello is sent by Mudlet as a greeting
# (see https://wiki.mudlet.org/w/Manual:Technical_Manual)
external_discord_hello = _not_implemented


# GMCP Client.Gui is sent by Mudlet for gui setup.
client_gui = _not_implemented


# -------------------------------------------------------------------------
# Rich editor (EvEditor web frontend) OOB protocol
#
# A capable client (the webclient, and later MXP/GMCP clients) drives the
# in-game editor through these instead of the line-mode ``:``-commands. See
# evennia/utils/eveditor.py and .agents/docs/engine-architecture/editor.md.
# -------------------------------------------------------------------------


def _find_editor(session):
    """Return the live EvEditor for ``session``'s puppet/account, if any."""
    for obj in (session.get_puppet(), session.account):
        if obj is None:
            continue
        editor = getattr(getattr(obj, "ndb", None), "_eveditor", None)
        if editor is not None:
            return editor
    return None


def editor_client(session, *args, **kwargs):
    """Client announces whether it can render the rich editor.

    Sent once by the webclient's editor plugin on connect. Sets a session
    protocol flag the editor reads to decide whether to open its web frontend
    for this session (otherwise it falls back to the line editor). This is the
    capability handshake: without it, no session is web-routed, so the line
    editor keeps working for every client.

    Kwargs:
        supported (bool): whether the client provides an editor UI.
    """
    from evennia.server.client_negotiation import capability_flags

    flags = capability_flags("editor_client", kwargs)
    if flags is None:
        return False
    supported = flags["CLIENT_EDITOR"]
    # Route through update_flags so the portal's authoritative copy also carries
    # the flag; a bare in-place assignment is dropped when portal_sessions_sync
    # rebuilds this session across ``@reload``, losing the web-editor capability.
    session.update_flags(CLIENT_EDITOR=supported)
    if supported:
        # If a web editor is already live on this body (e.g. the client just
        # reconnected after a refresh), re-open it on the new session so the
        # panel returns with its content instead of being silently stranded.
        editor = _find_editor(session)
        if editor is not None and getattr(editor, "_frontend", None) == "web":
            editor.reopen_web(session)


def narrative_client(session, *args, **kwargs):
    """Client announces whether it renders structured narrative nodes (R1/W1).

    Sets the ``CLIENT_NARRATIVE`` protocol flag that
    :func:`evennia.narrative.rendernode.deliver_node` reads to decide whether to
    send an emote/pose as a structured ``narrative`` payload or as a plain text
    line. Without it, every client keeps getting text, so this never regresses an
    un-upgraded client.

    Kwargs:
        supported (bool): whether the client renders narrative nodes.
    """
    from evennia.server.client_negotiation import capability_flags

    flags = capability_flags("narrative_client", kwargs)
    if flags is None:
        return False
    session.update_flags(**flags)
    return True


def azaban_hello(session, *args, **kwargs):
    """Azaban shell capability handshake (the `hello` envelope).

    Stores the client's declared capabilities and derives the delivery flags from
    them. ``caps.rendersNodes`` sets ``CLIENT_NARRATIVE`` so
    :func:`evennia.narrative.rendernode.deliver_node` ships structured ``render``
    payloads to this session; other caps (patches, assets, encoding) are stashed
    for the features that consume them. Unknown caps are ignored.

    Kwargs:
        caps (dict): the client capability map from the hello envelope.

    Returns:
        bool: ``True`` after the capabilities have been synchronized.
    """
    from evennia.server.client_negotiation import capability_flags

    flags = capability_flags("azaban_hello", kwargs)
    if flags is None:
        return False
    session.update_flags(**flags)
    return True


def editor_save(session, *args, **kwargs):
    """Save the editor buffer from a rich-client Save action.

    Kwargs:
        session_id (str): the editor session id from ``editor_open`` (validated).
        content (str): the full buffer content to save.
        close (bool): if true, also close the editor after saving.
    """
    session_id = kwargs.get("session_id")
    editor = _find_editor(session)
    if editor is None or not editor.web_save(
        kwargs.get("content", ""),
        session_id=session_id,
        close=bool(kwargs.get("close")),
    ):
        # No live editor, or a stale/mismatched panel: tell this client to
        # dismiss its dead panel rather than leaving it stuck open.
        session.msg(editor_close=([session_id], {}))


def editor_cancel(session, *args, **kwargs):
    """Close the editor without saving, from a rich-client Cancel action.

    Kwargs:
        session_id (str): the editor session id from ``editor_open`` (validated).
        discard (bool): confirm discarding unsaved changes. Without it, an
            unsaved buffer is kept and an ``unsaved`` status is sent instead.
    """
    session_id = kwargs.get("session_id")
    editor = _find_editor(session)
    if editor is None or not editor.web_cancel(
        session_id=session_id, discard=bool(kwargs.get("discard"))
    ):
        session.msg(editor_close=([session_id], {}))
