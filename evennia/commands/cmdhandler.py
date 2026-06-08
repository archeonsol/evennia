"""
Command handler

This module contains the infrastructure for accepting commands on the
command line. The processing of a command works as follows:

1. The calling object (caller) is analyzed based on its callertype.
2. Cmdsets are gathered from different sources:
   - object cmdsets: all objects at caller's location are scanned for non-empty
     cmdsets. This includes cmdsets on exits.
   - caller: the caller is searched for its own currently active cmdset.
   - account: lastly the cmdsets defined on caller.account are added.
3. The collected cmdsets are merged together to a combined, current cmdset.
4. If the input string is empty -> check for CMD_NOINPUT command in
   current cmdset or fallback to error message. Exit.
5. The Command Parser is triggered, using the current cmdset to analyze the
   input string for possible command matches.
6. If multiple matches are found -> check for CMD_MULTIMATCH in current
   cmdset, or fallback to error message. Exit.
7. If no match was found -> check for CMD_NOMATCH in current cmdset or
   fallback to error message. Exit.
8. At this point we have found a normal command. We assign useful variables to it that
   will be available to the command coder at run-time.
9. We have a unique cmdobject, primed for use. Call all hooks in order:
   `at_pre_parse()`, `cmdobj.parse()`, `at_pre_cmd()`, `cmdobj.func()`,
   `at_post_cmd()`. (`at_pre_cmd` runs after parse since 6.0.0+underspire.2;
   the old pre-parse hook was renamed to `at_pre_parse`.)
10. Return deferred that will fire with the return from `cmdobj.func()` (unused by default).

"""

import inspect
import time
import types
from collections import OrderedDict, defaultdict
from copy import copy
from itertools import chain
from traceback import format_exc

from django.conf import settings
from django.utils.translation import gettext as _
from ftfy import fix_text as _ftfy_fix_text
from twisted.internet import reactor
from twisted.internet.defer import ensureDeferred, inlineCallbacks
from twisted.internet.task import deferLater

from evennia.commands.cmdset import CmdSet
from evennia.commands.command import InterruptCommand
from evennia.commands.signals import (
    on_cmdset_merge_error,
    on_command_error,
    on_command_post,
    on_command_pre,
)
from evennia.utils import logger, utils
from evennia.utils.command_trace import get_trace_id

__all__ = ("cmdhandler", "InterruptCommand")
_GA = object.__getattribute__
# Cache mapping content-based fingerprint tuples to merged CmdSet results.
# When full, the least recently used entry is evicted.
# On a cache hit the *same* CmdSet instance is returned to every caller whose
# cmdsets match the fingerprint.  This is safe because cmdhandler copies each
# command before execution (copy(cmd) at dispatch time).  Code that inspects
# `merged_from` on a cached result will see the cmdsets from the *original*
# cache miss, not the current caller's — the content is equivalent.
# Note: this cache holds strong references to the merged CmdSets (and
# transitively to the cmd.obj / cmdsetobj stored in each fingerprint).
_CMDSET_MERGE_CACHE = OrderedDict()

# tracks recursive calls by each caller
# to avoid infinite loops (commands calling themselves)
_COMMAND_NESTING = defaultdict(lambda: 0)
_COMMAND_RECURSION_LIMIT = 10

# This decides which command parser is to be used.
# You have to restart the server for changes to take effect.
_COMMAND_PARSER = utils.variable_from_module(*settings.COMMAND_PARSER.rsplit(".", 1))

# System command names - import these variables rather than trying to
# remember the actual string constants. If not defined, Evennia
# hard-coded defaults are used instead.

# command to call if user just presses <return> with no input
CMD_NOINPUT = "__noinput_command"
# command to call if no command match was found
CMD_NOMATCH = "__nomatch_command"
# command to call if multiple command matches were found
CMD_MULTIMATCH = "__multimatch_command"
# command to call as the very first one when the user connects.
# (is expected to display the login screen)
CMD_LOGINSTART = "__unloggedin_look_command"


# Function for handling multiple command matches.
_SEARCH_AT_RESULT = utils.variable_from_module(*settings.SEARCH_AT_RESULT.rsplit(".", 1))

# Output strings. The first is the IN_GAME_ERRORS return, the second
# is the normal "production message to echo to the account.

_ERROR_UNTRAPPED = (
    _("""
An untrapped error occurred.
"""),
    _("""
An untrapped error occurred. Please file a bug report detailing the steps to reproduce.
"""),
)

_ERROR_CMDSETS = (
    _("""
A cmdset merger-error occurred. This is often due to a syntax
error in one of the cmdsets to merge.
"""),
    _("""
A cmdset merger-error occurred. Please file a bug report detailing the
steps to reproduce.
"""),
)

_ERROR_NOCMDSETS = (
    _("""
No command sets found! This is a critical bug that can have
multiple causes.
"""),
    _("""
No command sets found! This is a sign of a critical bug.  If
disconnecting/reconnecting doesn't" solve the problem, try to contact
the server admin through" some other means for assistance.
"""),
)

_ERROR_CMDHANDLER = (
    _("""
A command handler bug occurred. If this is not due to a local change,
please file a bug report with the Evennia project, including the
traceback and steps to reproduce.
"""),
    _("""
A command handler bug occurred. Please notify staff - they should
likely file a bug report with the Evennia project.
"""),
)

_ERROR_RECURSION_LIMIT = _(
    "Command recursion limit ({recursion_limit}) reached for '{raw_cmdname}' ({cmdclass})."
)


# delayed imports
_GET_INPUT = None


# helper functions
def err_helper(raw_string, cmdid=None):
    if cmdid is not None:
        return raw_string, {"cmdid": cmdid}
    return raw_string


def _msg_err(receiver, stringtuple, cmdid=None):
    """
    Helper function for returning an error to the caller.

    Args:
        receiver (Object): object to get the error message.
        stringtuple (tuple): tuple with two strings - one for the
            IN_GAME_ERRORS mode (with the traceback) and one with the
            production string (with a timestamp) to be shown to the user.

    """
    string = _("{traceback}\n{errmsg}\n(Traceback was logged {timestamp}).")
    timestamp = logger.timeformat()
    tracestring = format_exc()
    logger.log_trace()
    if settings.IN_GAME_ERRORS:
        out = string.format(
            traceback=tracestring, errmsg=stringtuple[0].strip(), timestamp=timestamp
        ).strip()
    else:
        out = string.format(
            traceback=tracestring.splitlines()[-1],
            errmsg=stringtuple[1].strip(),
            timestamp=timestamp,
        ).strip()
    receiver.msg(err_helper(out, cmdid=cmdid))


def _fire_cmdset_merge_error(caller, session, raw_string, exc):
    """Dispatch on_cmdset_merge_error for a failure during cmdset build/merge.

    Call this from within an active except handler so format_exc() captures
    the current traceback.
    """
    on_cmdset_merge_error.send_robust(
        sender=type(caller),
        caller=caller,
        session=session,
        raw_string=raw_string,
        trace_id=get_trace_id(),
        exc=exc,
        traceback_text=format_exc(),
    )


def _resolve_signal_session(session, cmdset_providers):
    """Resolve the session value exposed to signal receivers and ``cmd.session``.

    A session-proxy (e.g. multipuppet relay) is welcome at the cmdhandler
    boundary but should not leak into signal kwargs or ``cmd.session``
    those consumers want the real ``ServerSession`` (or ``None``).
    Proxies expose the underlying session via a ``real_session``
    attribute by convention; real sessions do not, so the ``getattr``
    is a no-op for them.

    Resolution order:
    1. If ``session`` was passed in, return its ``real_session`` if
       present, otherwise the value itself. The proxy is trusted to
       know which underlying session it wraps; we do not second-guess
       by looking at ``cmdset_providers`` (a multi-session caller could
       have providers that disagree with the proxy's intended session).
    2. Otherwise, fall back to ``cmdset_providers["session"]``
       (populated by ``generate_cmdset_providers`` from ``called_by``).
       Apply the same ``real_session`` unwrap for symmetry.
    3. Else ``None``.

    Returns:
        The resolved session, or ``None`` if no session is involved.
    """
    if session is not None:
        return getattr(session, "real_session", session)
    fallback = cmdset_providers.get("session")
    if fallback is not None:
        return getattr(fallback, "real_session", fallback)
    return None


def _normalize_account_command_caller(cmd, caller, cmdset_providers):
    """Normalise runtime attrs for ``AccountCommand`` dispatch.

    DEPRECATED (CM1 I1 closeout): only reached on the legacy ``cmdobj=`` /
    ``_run_command`` path; engine-routed input gets the same context from
    ``Actor.from_caller(callertype=...)`` (``evennia/actions/actor.py``). Delete
    this together with the ``account_command_caller`` flag when the legacy cmdset
    dispatch path is removed (CM1 input-capture migration finish line; ``.73``
    deprecation note).

    No-op unless ``cmd.account_command_caller`` is truthy. For account
    commands, rewrites:

    - ``cmd.caller`` to the Account (from ``cmdset_providers["account"]``,
      falling back to ``getattr(caller, "account", None)``).
    - ``cmd.account`` to the same Account (alias of ``cmd.caller``).
    - ``cmd.character`` to the puppet for the dispatching session
      (``cmdset_providers.get("object")``), or ``None`` if no puppet.

    If no Account is resolvable, ``cmd.caller`` is left untouched and
    ``cmd.character`` is set to ``None`` so the attribute always exists.

    Called once inside ``_run_command`` before ``_testing`` returns and
    before ``at_pre_parse``, so all hooks (and the test fixture in
    ``EvenniaCommandTestMixin.call``) observe a consistent shape.

    Args:
        cmd (Command): Command instance being prepared for dispatch.
        caller (Object, Account or Session): Pre-normalisation caller as
            chosen by ``generate_cmdset_providers``.
        cmdset_providers (dict): ``{cmdset_provider_type: provider}`` map.
    """
    if not getattr(cmd, "account_command_caller", False):
        return
    account = cmdset_providers.get("account") or getattr(caller, "account", None)
    if account is not None:
        cmd.caller = account
        cmd.account = account
    cmd.character = cmdset_providers.get("object")


def _process_input(caller, prompt, result, cmd, generator):
    """
    Specifically handle the get_input value to send to _progressive_cmd_run as
    part of yielding from a Command's `func`.

    Args:
        caller (Character, Account or Session): the caller.
        prompt (str): The sent prompt.
        result (str): The unprocessed answer.
        cmd (Command): The command itself.
        generator (GeneratorType): The generator.

    Returns:
        result (bool): Always `False` (stop processing).

    """
    # We call it using a Twisted deferLater to make sure the input is properly closed.
    deferLater(reactor, 0, _progressive_cmd_run, cmd, generator, response=result)
    return False


def _progressive_cmd_run(cmd, generator, response=None):
    """
    Progressively call the command that was given in argument. Used
    when `yield` is present in the Command's `func()` method.

    Args:
        cmd (Command): the command itself.
        generator (GeneratorType): the generator describing the processing.
        reponse (str, optional): the response to send to the generator.

    Raises:
        ValueError: If the func call yields something not identifiable as a
            time-delay or a string prompt.

    Note:
        This function is responsible for executing the command, if
        the func() method contains 'yield' instructions.  The yielded
        value will be accessible at each step and will affect the
        process.  If the value is a number, just delay the execution
        of the command.  If it's a string, wait for the user input.

    """
    global _GET_INPUT
    if not _GET_INPUT:
        from evennia.actions.menus import get_input as _GET_INPUT

    try:
        if response is None:
            value = next(generator)
        else:
            value = generator.send(response)
    except StopIteration:
        # duplicated from cmdhandler._run_command, to have these
        # run in the right order while staying inside the deferred
        cmd.at_post_cmd()
        # fire on_command_post for the generator path. t0 and trace_id were
        # stashed on the cmd in _run_command before the generator started.
        t0 = getattr(cmd, "_signal_t0", None)
        if t0 is not None:
            on_command_post.send_robust(
                sender=type(cmd),
                cmd=cmd,
                caller=cmd.caller,
                session=getattr(cmd, "session", None),
                trace_id=getattr(cmd, "_signal_trace_id", None),
                elapsed_ms=(time.monotonic() - t0) * 1000.0,
            )
        if cmd.save_for_next:
            # store a reference to this command, possibly
            # accessible by the next command.
            cmd.caller.ndb.last_cmd = copy(cmd)
        else:
            cmd.caller.ndb.last_cmd = None
    else:
        if isinstance(value, (int, float)):
            utils.delay(value, _progressive_cmd_run, cmd, generator)
        elif isinstance(value, str):
            _GET_INPUT(
                cmd.caller, value, _process_input, session=cmd.session, cmd=cmd, generator=generator
            )
        else:
            raise ValueError("unknown type for a yielded value in command: {}".format(type(value)))


# custom Exceptions


class NoCmdSets(Exception):
    "No cmdsets found. Critical error."

    pass


class ExecSystemCommand(Exception):
    "Run a system command"

    def __init__(self, syscmd, sysarg):
        self.args = (syscmd, sysarg)  # needed by exception error handling
        self.syscmd = syscmd
        self.sysarg = sysarg


class ErrorReported(Exception):
    """Re-raised when a substructure already reported the error.

    Carries the current ``command_trace`` id (if any) on ``trace_id`` so
    external error handlers can correlate the reported error with
    structured logs.
    """

    def __init__(self, raw_string):
        from evennia.utils.command_trace import get_trace_id

        self.args = (raw_string,)
        self.raw_string = raw_string
        self.trace_id = get_trace_id()


# Helper function
def generate_cmdset_providers(called_by, session=None):
    cmdset_providers = dict()
    cmdset_providers.update(called_by.get_cmdset_providers())
    if session and session is not called_by:
        cmdset_providers.update(session.get_cmdset_providers())

    cmdset_providers_list = list(cmdset_providers.values())
    cmdset_providers_list.sort(key=lambda x: getattr(x, "cmdset_provider_order", 0))
    # sort the dictionary by priority. This can be done because Python now cares about dictionary insert order.
    cmdset_providers = {c.cmdset_provider_type: c for c in cmdset_providers_list}

    if not cmdset_providers:
        raise RuntimeError("cmdhandler: no command objects found.")

    # the caller will be the one to receive messages and excert its permissions.
    # we assign the caller with preference 'bottom up'
    caller = cmdset_providers_list[-1]

    cmdset_providers_errors_list = sorted(
        cmdset_providers_list, key=lambda x: getattr(x, "cmdset_provider_error_order", 0)
    )

    # The error_to is the default recipient for errors. Tries to make sure an account
    # does not get spammed for errors while preserving character mirroring.
    error_to = cmdset_providers_errors_list[-1]

    return cmdset_providers, cmdset_providers_list, cmdset_providers_errors_list, caller, error_to


@inlineCallbacks
def get_and_merge_cmdsets(
    caller, cmdset_providers, callertype, raw_string, report_to=None, cmdid=None, session=None
):
    """
    Gather all relevant cmdsets and merge them.

    Args:
        caller (Session, Account or Object): The entity executing the command. Which
            type of object this is depends on the current game state; for example
            when the user is not logged in, this will be a Session, when being OOC
            it will be an Account and when puppeting an object this will (often) be
            a Character Object. In the end it depends on where the cmdset is stored.
        cmdset_providers (list): A list of sorted objects which provide cmdsets.
        callertype (str): This identifies caller as either "account", "object" or "session"
            to avoid having to do this check internally.
        raw_string (str): The input string. This is only used for error reporting.
        report_to (Object, optional): If given, this object will receive error messages

    Returns:
        cmdset (Deferred): This deferred fires with the merged cmdset
        result once merger finishes.

    Notes:
        The cdmsets are merged in order or generality, so that the
        Object's cmdset is merged last (and will thus take precedence
        over same-named and same-prio commands on Account and Session).

    """
    try:

        @inlineCallbacks
        def _get_local_obj_cmdsets(obj):
            """
            Helper-method; Get Object-level cmdsets

            """
            # Gather cmdsets from location, objects in location or carried
            try:
                local_obj_cmdsets = []
                try:
                    location = obj.location
                except Exception:
                    location = None
                if location:
                    from evennia.commands.location_cmdset_cache import (
                        get_cached_location_cmdsets,
                        make_cache_key,
                        set_cached_location_cmdsets,
                    )

                    loc_cache_key = make_cache_key(caller, location)
                    local_objlist = get_cached_location_cmdsets(loc_cache_key)
                    if local_objlist is None:
                        # Gather all cmdsets stored on objects in the room and
                        # also in the caller's inventory and the location itself
                        local_objlist = yield (
                            location.contents_get(exclude=obj) + obj.contents_get() + [location]
                        )
                        local_objlist = [
                            o
                            for o in local_objlist
                            if not o._is_deleted
                            and o.access(caller, access_type="call", no_superuser_bypass=True)
                        ]
                        # the call-type lock is checked here, it makes sure an account
                        # is not seeing e.g. the commands on a fellow account (which is
                        # why no_superuser_bypass must be True). Result is cached so
                        # repeat dispatches from the same caller in the same location
                        # skip the contents_get DB queries and the access filter.
                        set_cached_location_cmdsets(loc_cache_key, local_objlist)
                    # at_cmdset_get is a documented dynamic hook; it MUST fire on
                    # every dispatch (not just cache miss) so user code can mutate
                    # the cmdset stack per-call. We re-read .cmdset_stack after
                    # firing so the hook's mutations are honoured.
                    for lobj in local_objlist:
                        try:
                            _GA(lobj, "at_cmdset_get")(caller=caller)
                        except Exception:
                            logger.log_trace()
                    raw_obj_cmdsets = yield list(
                        chain.from_iterable(
                            lobj.cmdset.cmdset_stack
                            for lobj in local_objlist
                            if lobj.cmdset.current
                        )
                    )
                    # Shallow-copy each cset before applying the room-gather
                    # ``duplicates`` rule. The csets live on the objects
                    # themselves and are shared across all dispatches; mutating
                    # ``duplicates`` directly raced concurrent dispatches that
                    # interleave through ``yield`` points in the merge loop.
                    # Copies own their own attribute so callers don't share
                    # state. Commands list is shared by reference (merge code
                    # rebuilds lists, never mutates in place).
                    local_obj_cmdsets = []
                    for cset in raw_obj_cmdsets:
                        cset_copy = copy(cset)
                        # The room-gather merge rule: an unset (``None``)
                        # ``duplicates`` is treated as True so commands from
                        # multiple objects in the same room can coexist
                        # ("two balls in a room"). Explicit True/False values
                        # set by the cmdset author are preserved.
                        if cset_copy.duplicates is None:
                            cset_copy.duplicates = True
                        local_obj_cmdsets.append(cset_copy)
                return local_obj_cmdsets
            except Exception as exc:
                _fire_cmdset_merge_error(caller, session, raw_string, exc)
                _msg_err(caller, _ERROR_CMDSETS)
                raise ErrorReported(raw_string)

        @inlineCallbacks
        def _get_cmdsets(obj, current):
            """
            Helper method; Get cmdset while making sure to trigger all
            hooks safely. Returns the stack and the valid options.

            """
            try:
                yield obj.at_cmdset_get(caller=caller, current=current)
            except Exception as exc:
                _fire_cmdset_merge_error(caller, session, raw_string, exc)
                _msg_err(caller, _ERROR_CMDSETS)
                raise ErrorReported(raw_string)
            try:
                return obj.get_cmdsets(caller=caller, current=current)
            except AttributeError:
                return (CmdSet(), [])

        local_obj_cmdsets = []

        current_cmdset = CmdSet()
        object_cmdsets = list()
        for cmdobj in cmdset_providers:
            current, cur_cmdsets = yield _get_cmdsets(cmdobj, current_cmdset)
            if current:
                current_cmdset = current_cmdset + current
            if cur_cmdsets:
                object_cmdsets += cur_cmdsets
            match cmdobj.cmdset_provider_type:
                case "object":
                    if not current.no_objs:
                        local_obj_cmdsets = yield _get_local_obj_cmdsets(cmdobj)
                        if current.no_exits:
                            # filter out all exits
                            local_obj_cmdsets = [
                                cmdset for cmdset in local_obj_cmdsets if cmdset.key != "ExitCmdSet"
                            ]
                        object_cmdsets += local_obj_cmdsets

        # weed out all non-found sets
        cmdsets = yield [
            cmdset for cmdset in object_cmdsets if cmdset and cmdset.key != "_EMPTY_CMDSET"
        ]
        # report cmdset errors to user (these should already have been logged)
        if report_to:
            yield [
                report_to.msg(err_helper(cmdset.errmessage, cmdid=cmdid))
                for cmdset in cmdsets
                if cmdset.key == "_CMDSET_ERROR"
            ]

        if cmdsets:
            # each cmdset caches its own fingerprint, so this is just a tuple lookup
            mergehash = tuple(cmdset.fingerprint for cmdset in cmdsets)
            if mergehash in _CMDSET_MERGE_CACHE:
                # cached merge exists; mark as recently used.
                # Note: the cached cmdset's `merged_from` still references the
                # cmdset objects from the original miss — not the current caller's.
                # Content is equivalent (same fingerprint), so this is harmless.
                _CMDSET_MERGE_CACHE.move_to_end(mergehash)
                cmdset = _CMDSET_MERGE_CACHE[mergehash]
            else:
                # we group and merge all same-prio cmdsets separately (this avoids
                # order-dependent clashes in certain cases, such as
                # when duplicates=True)
                tempmergers = {}
                for cmdset in cmdsets:
                    prio = cmdset.priority
                    if prio in tempmergers:
                        # merge same-prio cmdset together separately
                        tempmergers[prio] = yield tempmergers[prio] + cmdset
                    else:
                        tempmergers[prio] = cmdset

                # sort cmdsets after reverse priority (highest prio are merged in last)
                sorted_cmdsets = yield sorted(list(tempmergers.values()), key=lambda x: x.priority)

                # Merge all command sets into one, beginning with the lowest-prio one
                cmdset = sorted_cmdsets[0]
                for merging_cmdset in sorted_cmdsets[1:]:
                    cmdset = yield cmdset + merging_cmdset
                # store the original, ungrouped set for diagnosis
                cmdset.merged_from = cmdsets
                # Before caching, clear back-references that would pin game
                # objects in memory long after players log out.  merged_from
                # is diagnostic-only (acceptable loss for cache hits).
                # Individual cmd.obj refs are left intentionally — they are
                # needed during execution and the fingerprint key ensures stale
                # entries are never used after an object's cmdset changes.
                cached_set = cmdset
                cached_set.merged_from = []
                cached_set.cmdsetobj = None
                # cache; evict oldest entry if full
                _CMDSET_MERGE_CACHE[mergehash] = cached_set
                if len(_CMDSET_MERGE_CACHE) > settings.CMDSET_MERGE_CACHE_MAXSIZE:
                    _CMDSET_MERGE_CACHE.popitem(last=False)
        else:
            cmdset = None
        # Restore loop removed: ``local_obj_cmdsets`` are now shallow
        # copies, so the duplicates mutation has no shared-state impact.
        # important - this syncs the CmdSetHandler's .current field with the
        # true current cmdset!
        # TODO - removed because this causes cmdset overlaps across sessions/accounts
        # - see https://github.com/evennia/evennia/issues/2855
        # if cmdset:
        #     caller.cmdset.current = cmdset

        return cmdset
    except ErrorReported:
        raise
    except Exception as exc:
        _fire_cmdset_merge_error(caller, session, raw_string, exc)
        _msg_err(caller, _ERROR_CMDSETS)
        raise
        # raise ErrorReported


# Main command-handler function


@inlineCallbacks
def cmdhandler(
    called_by,
    raw_string,
    _testing=False,
    callertype="session",
    session=None,
    cmdobj=None,
    cmdobj_key=None,
    **kwargs,
):
    """
    This is the main mechanism that handles any string sent to the engine.

    Args:
        called_by (Session, Account or Object): Object from which this
            command was called. which this was called from.  What this is
            depends on the game state.
        raw_string (str): The command string as given on the command line.
        _testing (bool, optional): Used for debug purposes and decides if we
            should actually execute the command or not. If True, the
            command instance will be returned.
        callertype (str, optional): One of "session", "account" or
            "object". These are treated in decending order, so when the
            Session is the caller, it will merge its own cmdset into
            cmdsets from both Account and eventual puppeted Object (and
            cmdsets in its room etc). An Account will only include its own
            cmdset and the Objects and so on. Merge order is the same
            order, so that Object cmdsets are merged in last, giving them
            precendence for same-name and same-prio commands.
        session (Session, optional): Relevant if callertype is "account" - the session will help
            retrieve the correct cmdsets from puppeted objects. Any object
            implementing the session-proxy contract is accepted: a
            ``get_cmdset_providers()`` method returning a dict (same shape
            as ``ServerSession.get_cmdset_providers()``) is required;
            ``puppet`` / ``account`` / ``sessid`` attributes are read by
            downstream callers when present. This allows the multipuppet
            relay (and similar) to construct a lightweight proxy and pass
            it in instead of monkey-patching ``Command``.
        cmdobj (Command, optional): If given a command instance, this will be executed using
            `called_by` as the caller, `raw_string` representing its arguments and (optionally)
            `cmdobj_key` as its input command name. No cmdset lookup will be performed but
            all other options apply as normal. This allows for running a specific Command
            within the command system mechanism.
        cmdobj_key (string, optional): Used together with `cmdobj` keyword to specify
            which cmdname should be assigned when calling the specified Command instance. This
            is made available as `self.cmdstring` when the Command runs.
            If not given, the command will be assumed to be called as `cmdobj.key`.

    Keyword Args:
        kwargs (any): other keyword arguments will be assigned as named variables on the
            retrieved command object *before* it is executed. This is unused
            in default Evennia but may be used by code to set custom flags or
            special operating conditions for a command as it executes.

    Returns:
        deferred (Deferred): This deferred is fired with the return
        value of the command's `func` method.  This is not used in
        default Evennia.

    """
    cmdid = kwargs.get("cmdid", None)

    # Repair mojibake / bad-encoding artefacts before any downstream stage
    # observes the input. Runs ahead of generate_cmdset_providers so that
    # signal payloads, the cmdset merge, the parser, and cmd.raw_string all
    # see the normalised text. Gated on INPUT_FTFY_NORMALIZE (default True).
    # Pure-ASCII input skips ftfy entirely: ftfy can't repair anything in
    # ASCII text, and `str.isascii()` is a C-level check that beats letting
    # ftfy do its own scan-then-bail.
    if (
        isinstance(raw_string, str)
        and not raw_string.isascii()
        and getattr(settings, "INPUT_FTFY_NORMALIZE", True)
    ):
        raw_string = _ftfy_fix_text(raw_string)

    @inlineCallbacks
    def _run_command(cmd, cmdname, args, raw_cmdname, cmdset, session, account, cmdset_providers):
        """
        Helper function: This initializes and runs the Command
        instance once the parser has identified it as either a normal
        command or one of the system commands.

        Args:
            cmd (Command): Command object
            cmdname (str): Name of command
            args (str): extra text entered after the identified command
            raw_cmdname (str): Name of Command, unaffected by eventual
                prefix-stripping (if no prefix-stripping, this is the same
                as cmdname).
            cmdset (CmdSet): Command sert the command belongs to (if any)..
            session (Session): Session of caller (if any).
            account (Account): Account of caller (if any).
            cmdset_providers (dict): Dictionary of all cmdset-providing objects.

        Returns:
            deferred (Deferred): this will fire with the return of the
                command's `func` method.

        Raises:
            RuntimeError: If command recursion limit was reached.

        """
        global _COMMAND_NESTING
        try:
            from django.conf import settings as _settings

            if getattr(_settings, "COMMAND_TRACE_ENABLED", True):
                from evennia.utils.command_trace import begin_command_trace

                begin_command_trace(
                    caller=caller,
                    session=session,
                    raw_string=unformatted_raw_string,
                    cmd_key=raw_cmdname,
                )
        except Exception:
            pass
        try:
            # Assign useful variables to the instance
            cmd.caller = caller
            cmd.cmdname = cmdname
            cmd.raw_cmdname = raw_cmdname
            cmd.cmdstring = cmdname  # deprecated
            cmd.args = args
            cmd.cmdset = cmdset
            cmd.cmdset_providers = cmdset_providers.copy()
            cmd.session = session
            cmd.account = account
            cmd.raw_string = unformatted_raw_string
            # cmd.obj  # set via on-object cmdset handler for each command,
            # since this may be different for every command when
            # merging multiple cmdsets

            # AccountCommand normalisation: rewrite caller/account/character
            # before _testing returns and before at_pre_parse, so all hooks
            # (and the _testing path) observe consistent state. No-op for
            # ordinary Command subclasses.
            _normalize_account_command_caller(cmd, caller, cmdset_providers)

            if _testing:
                # only return the command instance
                return cmd

            # assign custom kwargs to found cmd object
            for key, val in kwargs.items():
                setattr(cmd, key, val)

            _COMMAND_NESTING[called_by] += 1
            if _COMMAND_NESTING[called_by] > _COMMAND_RECURSION_LIMIT:
                err = _ERROR_RECURSION_LIMIT.format(
                    recursion_limit=_COMMAND_RECURSION_LIMIT,
                    raw_cmdname=raw_cmdname,
                    cmdclass=cmd.__class__,
                )
                raise RuntimeError(err)

            # Wall-clock and trace-id captured up front, before at_pre_parse
            # so on_command_post.elapsed_ms covers the full hook range.
            # Stashed on cmd for the generator path; _progressive_cmd_run
            # cannot read the contextvar after end_command_trace() runs.
            _signal_t0 = time.monotonic()
            _signal_trace_id = get_trace_id()
            cmd._signal_t0 = _signal_t0
            cmd._signal_trace_id = _signal_trace_id

            on_command_pre.send_robust(
                sender=type(cmd),
                cmd=cmd,
                caller=caller,
                session=session,
                trace_id=_signal_trace_id,
            )

            # pre-parse hook (was: at_pre_cmd before 6.0.0+underspire.2)
            abort = yield cmd.at_pre_parse()
            if abort:
                # abort sequence
                return abort

            # Parse and execute
            yield cmd.parse()

            # post-parse, pre-func hook (new in 6.0.0+underspire.2)
            abort = yield cmd.at_pre_cmd()
            if abort:
                return abort

            # main command code
            # (return value is normally None)
            ret = cmd.func()
            if isinstance(ret, types.GeneratorType):
                # cmd.func() is a generator, execute progressively.
                # _progressive_cmd_run handles at_post_cmd and on_command_post.
                _progressive_cmd_run(cmd, ret)
            else:
                if inspect.iscoroutine(ret):
                    # async def func(self) — wait for completion before post-hooks.
                    yield ensureDeferred(ret)

                # post-command hook (sync and async paths both land here)
                yield cmd.at_post_cmd()

                on_command_post.send_robust(
                    sender=type(cmd),
                    cmd=cmd,
                    caller=caller,
                    session=session,
                    trace_id=_signal_trace_id,
                    elapsed_ms=(time.monotonic() - _signal_t0) * 1000.0,
                )

                if cmd.save_for_next:
                    # store a reference to this command, possibly
                    # accessible by the next command.
                    caller.ndb.last_cmd = yield copy(cmd)
                else:
                    caller.ndb.last_cmd = None

        except InterruptCommand:
            # Do nothing, clean exit
            pass
        except Exception as exc:
            tb_text = format_exc()
            on_command_error.send_robust(
                sender=type(cmd),
                cmd=cmd,
                caller=caller,
                session=session,
                trace_id=get_trace_id(),
                exc=exc,
                traceback_text=tb_text,
            )
            _msg_err(caller, _ERROR_UNTRAPPED)
            raise ErrorReported(cmd.raw_string)
        finally:
            _COMMAND_NESTING[called_by] -= 1
            if _COMMAND_NESTING[called_by] <= 0:
                _COMMAND_NESTING.pop(called_by, None)
            try:
                from django.conf import settings as _settings

                if getattr(_settings, "COMMAND_TRACE_ENABLED", True):
                    from evennia.utils.command_trace import end_command_trace

                    end_command_trace()
            except Exception:
                pass

    # Action engine bridge (CM1 Phase 8): all normal player input dispatches
    # through the engine and never merges cmdsets. The legacy cmdset path below
    # is reached only for ``cmdobj=`` injection (a specific Command run directly,
    # e.g. the login ``connect`` command or contrib menu commands), which must
    # bypass the bridge so its ``func`` actually runs.
    from evennia.actions.dispatch import try_action_dispatch

    if cmdobj is None:
        handled = yield try_action_dispatch(
            called_by, raw_string, session=session, callertype=callertype, **kwargs
        )
        if handled:
            return

    (
        cmdset_providers,
        cmdset_providers_list,
        cmdset_providers_list_error,
        caller,
        error_to,
    ) = generate_cmdset_providers(called_by, session=session)

    # Resolve `session` once so signal payloads and ``cmd.session``
    # always carry the real ServerSession when one exists (or None).
    # Before this, callertype="session" left `session=None` even when
    # `called_by` was itself a session, surfacing as a footgun in
    # downstream signal receivers and `cmd.session` users.
    session = _resolve_signal_session(session, cmdset_providers)

    account = cmdset_providers.get("account", None)

    try:  # catch bugs in cmdhandler itself
        try:  # catch special-type commands
            if cmdobj:
                # the command object is already given
                cmd = cmdobj() if callable(cmdobj) else cmdobj
                cmdname = cmdobj_key if cmdobj_key else cmd.key
                args = raw_string
                unformatted_raw_string = "%s%s" % (cmdname, args)
                cmdset = None
                raw_cmdname = cmdname
                # session = session
                # account = account

            else:
                # no explicit cmdobject given, figure it out
                cmdset = yield get_and_merge_cmdsets(
                    caller,
                    cmdset_providers_list,
                    callertype,
                    raw_string,
                    cmdid=cmdid,
                    session=session,
                )
                if not cmdset:
                    # this is bad and shouldn't happen.
                    raise NoCmdSets
                # store the completely unmodified raw string - including
                # whitespace and eventual prefixes-to-be-stripped.
                unformatted_raw_string = raw_string
                raw_string = raw_string.strip()
                if not raw_string:
                    # Empty input. Test for system command instead.
                    syscmd = yield cmdset.get(CMD_NOINPUT)
                    sysarg = ""
                    raise ExecSystemCommand(syscmd, sysarg)
                # Parse the input string and match to available cmdset.
                # This also checks for permissions, so all commands in match
                # are commands the caller is allowed to call.
                try:
                    matches = yield _COMMAND_PARSER(raw_string, cmdset, caller, session=session)
                except TypeError:
                    logger.log_dep(
                        "Custom cmdparser does not accept 'session' kwarg. "
                        "Update its signature to cmdparser(raw_string, cmdset, caller, "
                        "match_index=None, session=None, **kwargs). "
                        "Session-aware lock functions like is_ooc() will not "
                        "work correctly until this is fixed."
                    )
                    matches = yield _COMMAND_PARSER(raw_string, cmdset, caller)

                # Deal with matches

                if len(matches) > 1:
                    # We have a multiple-match
                    syscmd = yield cmdset.get(CMD_MULTIMATCH)
                    sysarg = _("There were multiple matches.")
                    if syscmd:
                        # use custom CMD_MULTIMATCH
                        syscmd.matches = matches
                    else:
                        # fall back to default error handling
                        sysarg = yield _SEARCH_AT_RESULT(
                            [match[2] for match in matches], caller, query=matches[0][0]
                        )
                    raise ExecSystemCommand(syscmd, sysarg)

                cmdname, args, cmd, raw_cmdname = "", "", None, ""
                if len(matches) == 1:
                    # We have a unique command match. But it may still be invalid.
                    match = matches[0]
                    cmdname, args, cmd, raw_cmdname = (match[0], match[1], match[2], match[5])

                if not matches:
                    # No commands match our entered command
                    syscmd = yield cmdset.get(CMD_NOMATCH)
                    if syscmd:
                        # use custom CMD_NOMATCH command
                        sysarg = raw_string
                    else:
                        # fallback to default error text
                        sysarg = _("Command '{command}' is not available.").format(
                            command=raw_string
                        )
                        suggestions = []
                        if getattr(settings, "COMMAND_FUZZY_SUGGESTIONS_ENABLED", True):
                            from evennia.commands.cmdparser_trie import (
                                fuzzy_command_suggestions,
                            )

                            suggestions = fuzzy_command_suggestions(raw_string, cmdset)
                        if suggestions:
                            sysarg += _(" Maybe you meant {command}?").format(
                                command=utils.list_to_string(
                                    suggestions, endsep=_("or"), addquote=True
                                )
                            )
                        else:
                            sysarg += _(' Type "help" for help.')
                    raise ExecSystemCommand(syscmd, sysarg)

            if not cmd.retain_instance:
                # making a copy allows multiple users to share the command also when yield is used
                cmd = copy(cmd)

            # A normal command.
            yield _run_command(
                cmd, cmdname, args, raw_cmdname, cmdset, session, account, cmdset_providers
            )

        except ErrorReported as exc:
            # this error was already reported, so we
            # catch it here and don't pass it on.
            logger.log_err("User input was: '%s'." % logger.mask_sensitive_input(exc.raw_string))

        except ExecSystemCommand as exc:
            # Not a normal command: run a system command, if available,
            # or fall back to a return string.
            syscmd = exc.syscmd
            sysarg = exc.sysarg

            if syscmd:
                yield _run_command(
                    syscmd,
                    syscmd.key,
                    sysarg,
                    unformatted_raw_string,
                    cmdset,
                    session,
                    account,
                    cmdset_providers,
                )
                return
            elif sysarg:
                # return system arg
                error_to.msg(err_helper(exc.sysarg, cmdid=cmdid))

        except NoCmdSets:
            # Critical error.
            logger.log_err("No cmdsets found: %s" % caller)
            error_to.msg(err_helper(_ERROR_NOCMDSETS, cmdid=cmdid))

        except Exception:
            # We should not end up here. If we do, it's a programming bug.
            _msg_err(error_to, _ERROR_UNTRAPPED)

    except Exception:
        # This catches exceptions in cmdhandler exceptions themselves
        _msg_err(error_to, _ERROR_CMDHANDLER)
