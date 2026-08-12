"""
The cmdhandler bridge (CM1 Phase 4): the seam between Evennia's ``cmdhandler``
and the action engine.

``cmdhandler`` calls :func:`try_action_dispatch` on every normal input line
(CM1 Phase 8). The engine is the sole dispatch path for player input; the
legacy cmdset machinery in ``cmdhandler`` remains only for ``cmdobj=``
injection (running a specific Command instance directly). The bridge returns
the dispatch's :class:`~evennia.actions.result.ActionTrace` so callers can see
what actually happened, and guarantees a parsed verb never ends in silence:
when a dispatch fires no ``carry_out``/``report`` rule, a fully
``requires``-gated verb is logged to the security log and re-dispatched as a
no-match (indistinguishable from a verb that does not exist), while a verb
with no responding rules at all gets a direct refusal message.

Routing rules (Phase 4, before any game verb is ported):

1. **Active capturing state** (EvMenu, disambiguation) → always route through the
   engine so the state's catch-all ``before`` rule sees the input.
2. **Verb matches a registered, non-system action** → dispatch it.
3. Otherwise with no active state: empty line → ``False`` (legacy
   ``__noinput__``). Unknown verb (``NoMatchAction``, confidence 0.0) → always
   ``_dispatch_with_signals`` (game ``NoMatchRules`` + default feedback). The
   Game ``NoMatchRules`` handle unknown verbs; legacy ``__nomatch__`` is unused.

:class:`AmbiguousTarget` raised mid-parse installs a
:class:`~evennia.actions.menus.DisambiguationState` (carrying the raw line) and
sends the choice prompt; the player's next line is resolved here (a search
override is stashed and the original line replayed), not inside the engine.

Signals (``on_command_pre`` / ``on_command_post`` / ``on_command_error``) fire
around engine dispatch so existing receivers keep working across both paths;
:class:`DispatchMiddleware` (e.g. :class:`ProfilingMiddleware`) wraps each
dispatch for timing/instrumentation.
"""

import time
from collections import defaultdict

from django.utils.translation import gettext as _

from evennia.commands.signals import on_command_error, on_command_post, on_command_pre
from evennia.utils.command_trace import get_trace_id

from .actor import Actor
from .context import build_context
from .engine import engine as _default_engine
from .exceptions import AmbiguousTarget
from .menus import DisambiguationState, _candidate_label, _parse_choice
from .parser import parser as _default_parser

__all__ = [
    "try_action_dispatch",
    "DispatchMiddleware",
    "ProfilingMiddleware",
    "register_middleware",
    "clear_middlewares",
    "get_middlewares",
]


# --------------------------------------------------------------------------- #
# Middleware
# --------------------------------------------------------------------------- #


class DispatchMiddleware:
    """Hook around a single engine dispatch. Subclass and register via
    :func:`register_middleware`. Both hooks are best-effort (exceptions are
    swallowed so instrumentation never breaks a dispatch)."""

    def before_dispatch(self, action, actor, context): ...

    def after_dispatch(self, action, actor, context, trace): ...


_middlewares = []


def register_middleware(middleware):
    """Add a :class:`DispatchMiddleware` to the global chain."""
    _middlewares.append(middleware)
    return middleware


def clear_middlewares():
    """Drop all registered middleware (mainly for tests)."""
    _middlewares.clear()


def get_middlewares():
    """The current middleware chain (live list)."""
    return _middlewares


class ProfilingMiddleware(DispatchMiddleware):
    """Records per-action-type dispatch timing. Replaces the old
    ``ProfilingCommandMixin``; works for both dispatch paths — engine dispatches
    via :meth:`before_dispatch`/:meth:`after_dispatch`, legacy commands via
    :meth:`on_command_post` (connect it to the ``on_command_post`` signal)."""

    def __init__(self):
        self.timings = defaultdict(list)
        self._starts = {}

    def before_dispatch(self, action, actor, context):
        self._starts[id(action)] = time.monotonic()

    def after_dispatch(self, action, actor, context, trace):
        t0 = self._starts.pop(id(action), None)
        if t0 is not None:
            self.record(type(action), (time.monotonic() - t0) * 1000.0)

    def record(self, action_type, elapsed_ms):
        self.timings[action_type].append(elapsed_ms)

    def on_command_post(self, sender=None, elapsed_ms=None, **kwargs):
        """Signal receiver for the legacy path (``sender`` is the Command type)."""
        if elapsed_ms is not None:
            self.record(sender, elapsed_ms)


# --------------------------------------------------------------------------- #
# Routing helpers
# --------------------------------------------------------------------------- #


def _format_disambiguation(candidates, looker=None) -> str:
    lines = [f"  {i + 1}: {_candidate_label(c, looker)}" for i, c in enumerate(candidates)]
    return "Which one did you mean?\n" + "\n".join(lines)


def _active_disambiguation(actor):
    for st in reversed(actor.state_objects):
        if isinstance(st, DisambiguationState):
            return st
    return None


# --------------------------------------------------------------------------- #
# Bridge entry point
# --------------------------------------------------------------------------- #


async def try_action_dispatch(
    called_by,
    raw_string,
    session=None,
    actor=None,
    engine=None,
    parser=None,
    callertype=None,
    **kwargs,
):
    """Attempt to dispatch ``raw_string`` through the action engine.

    Args:
        called_by: the legacy caller (session / account / object).
        raw_string (str): the input line.
        session: the originating session, if known.
        actor (Actor): pre-built actor (tests); built from ``called_by`` if None.
        engine (RuleEngine): override the engine singleton (tests).
        parser (ActionParser): override the parser singleton (tests).
        callertype (str): cmdhandler's ``"session"``/``"account"``/``"object"``
            discriminator, used to build the actor unambiguously.

    Returns:
        Deferred[ActionTrace | None]: fires with the dispatch's trace, or
        ``None`` when the bridge consumed the line without an engine dispatch
        (a disambiguation prompt was installed, or a pending choice was
        cancelled).
    """
    engine = engine or _default_engine
    parser = parser or _default_parser
    if actor is None:
        actor = Actor.from_caller(called_by, session, callertype=callertype)

    # 1) An active disambiguation resolves the choice here (the original line is
    #    replayed with a search override; see Actor.search).
    disambig = _active_disambiguation(actor)
    if disambig is not None and disambig.pending_raw is not None:
        trace = await _resolve_disambiguation(
            called_by, raw_string, session, actor, disambig, engine, parser, callertype, **kwargs
        )
        return trace

    # 2) Parse. AmbiguousTarget mid-parse → install state + prompt.
    try:
        parse_result = parser.parse(raw_string, actor)
    except AmbiguousTarget as exc:
        actor.enter_state(
            DisambiguationState(
                exc.candidates,
                pending_raw=raw_string,
                ambiguous_name=exc.original_raw,
            )
        )
        actor.msg(_format_disambiguation(exc.candidates, looker=getattr(actor, "character", None)))
        return None

    states_active = bool(actor.state_objects)

    # 3) Decide whether this input belongs to the engine.
    if parse_result is None:
        if not states_active:
            from .parser import NoInputAction

            action = NoInputAction()
        else:
            action = _carrier(raw_string)
    else:
        action = parse_result.action
        # Unknown verbs become NoMatchAction (confidence 0.0) and dispatch below.
        # Dynamic resolver hits (e.g. exit name → Move) use confidence 1.0 and
        # must not fall through to legacy.

    # Unlogged-in connect/create/etc. parse to normal actions, resolved by
    # SessionLoginRules (composed into ServerSession). The only special case is
    # the CMD_LOGINSTART sentinel, mapped to LoginStartAction so the engine
    # renders the connection screen on connect.
    if _is_unloggedin(actor):
        from evennia.commands.cmdhandler import CMD_LOGINSTART

        from .parser import LoginStartAction

        stripped = (raw_string or "").strip()
        if stripped == CMD_LOGINSTART:
            action = LoginStartAction()

    trace = await _dispatch_with_signals(
        action, actor, raw_string, session, engine, callertype=callertype
    )
    fallback = _fail_closed_fallback(action, actor, trace, raw_string)
    if fallback is not None:
        # A gated verb must be indistinguishable from a nonexistent one: route
        # the line through the real no-match pipeline (game NoMatchRules +
        # defaults). The trace returned is still the typed verb's — that's
        # what truthfully describes this input; the feedback dispatch is an
        # implementation detail.
        await _dispatch_with_signals(
            fallback, actor, raw_string, session, engine, callertype=callertype
        )
    return trace


def _is_unloggedin(actor) -> bool:
    return actor is not None and actor.account is None and actor.session is not None


def _fail_closed_fallback(action, actor, trace, raw_string):
    """Guarantee a parsed verb dispatch never ends in silence (fail closed).

    ``requires``-gated ``carry_out`` rules only SKIP, so without this a verb
    whose every carry_out path is permission-gated (e.g. a staff verb typed by
    a player, or by quelled staff) dispatches "successfully" with no output at
    all. Gates are the security boundary, so a fully-gated verb must look like
    a verb that does not exist: the attempt goes to the security log and the
    line is re-dispatched as a suggestion-free :class:`NoMatchAction` (returned
    here for the caller to dispatch). A verb with no responding rules at all is
    treated the same way, for the same reason: verbs are registered globally at
    import time but *provided* narrowly — by a room, a body, a state — so
    "nothing here answers this" is the ordinary shape of a context-gated verb
    that does not apply. Answering it distinctly ("You can't do that.") would
    confirm the verb exists to anyone who guesses the words, which is the leak
    the gated branch already avoids.

    System actions (no-match, no-input, login-start) ship their own default
    providers, and an ``_unresolved`` action's target miss was already
    reported by ``search``.

    Returns:
        NoMatchAction | None: the fallback action to dispatch, or ``None``
        when no fallback dispatch is needed.
    """
    from .parser import LoginStartAction, NoInputAction, NoMatchAction

    if trace is None or trace.outcome in ("blocked", "aborted"):
        return None
    # REDIRECTs swap the action mid-dispatch; judge what actually ran.
    final_action = trace.action if trace.action is not None else action
    if isinstance(final_action, (NoMatchAction, NoInputAction, LoginStartAction)):
        return None
    if final_action._unresolved:
        return None
    if trace.carry_out_fired or trace.report_fired:
        return None
    if trace.carry_out_gated:
        from evennia.utils import logger

        logger.log_sec(
            f"Denied (fail-closed): {type(final_action).__name__} "
            f"by {trace.actor_key}: input {logger.mask_sensitive_input(raw_string)!r}, "
            f"{trace.carry_out_gated} gated carry_out path(s)."
        )
    # No suggestions in either case: computing them would offer the hidden or
    # unavailable verb back. Only the gated branch is security-logged — an
    # inapplicable verb is not an attempt at anything.
    return NoMatchAction(raw_string=raw_string)


async def _resolve_disambiguation(
    called_by, raw_string, session, actor, state, engine, parser, callertype=None, **kwargs
):
    """Resolve a pending disambiguation from the player's choice line."""
    choice = _parse_choice(raw_string, state.candidates, looker=getattr(actor, "character", None))
    actor.exit_state(DisambiguationState)
    if choice is None:
        actor.msg("Invalid choice. Cancelled.")
        return None
    actor.set_search_override(state.ambiguous_name, choice)
    trace = await try_action_dispatch(
        called_by,
        state.pending_raw,
        session=session,
        actor=actor,
        engine=engine,
        parser=parser,
        callertype=callertype,
        **kwargs,
    )
    return trace


async def _dispatch_with_signals(action, actor, raw_string, session, engine, callertype=None):
    """Run one engine dispatch wrapped in signals + middleware."""
    action._raw_string = raw_string
    caller = actor.effective
    trace_id = get_trace_id()
    t0 = time.monotonic()

    on_command_pre.send_robust(
        sender=type(action), cmd=action, caller=caller, session=session, trace_id=trace_id
    )

    from .parser import NoMatchAction

    context = build_context(
        actor,
        raw_string,
        targets=action.targets,
        trace_id=trace_id,
        callertype=callertype,
        action_type=type(action),
    )
    if isinstance(action, NoMatchAction):
        from .default.nomatch import nomatch_providers

        context = type(context)(
            providers=[*nomatch_providers, *context.providers],
            actor=context.actor,
            raw_string=context.raw_string,
            trace_id=context.trace_id,
        )

    from .parser import LoginStartAction, NoInputAction

    if isinstance(action, LoginStartAction):
        from .default.loginstart import loginstart_providers

        context = type(context)(
            providers=[*loginstart_providers, *context.providers],
            actor=context.actor,
            raw_string=context.raw_string,
            trace_id=context.trace_id,
        )
    elif isinstance(action, NoInputAction):
        pass

    for mw in _middlewares:
        try:
            mw.before_dispatch(action, actor, context)
        except Exception:
            from evennia.utils import logger

            logger.log_trace()

    try:
        trace = await engine.dispatch(action, actor, context, record_phases=False)
    except Exception as exc:
        from traceback import format_exc

        on_command_error.send_robust(
            sender=type(action),
            cmd=action,
            caller=caller,
            session=session,
            trace_id=trace_id,
            exc=exc,
            traceback_text=format_exc(),
        )
        raise

    for mw in _middlewares:
        try:
            mw.after_dispatch(action, actor, context, trace)
        except Exception:
            from evennia.utils import logger

            logger.log_trace()

    on_command_post.send_robust(
        sender=type(action),
        cmd=action,
        caller=caller,
        session=session,
        trace_id=trace_id,
        elapsed_ms=(time.monotonic() - t0) * 1000.0,
    )
    return trace


def _carrier(raw_string):
    """A neutral Action carrying the raw input, for routing to a capturing
    state when no verb matched (e.g. a menu/disambiguation choice line)."""
    from .parser import NoMatchAction

    carrier = NoMatchAction(raw_string=raw_string)
    carrier._raw_string = raw_string
    return carrier
