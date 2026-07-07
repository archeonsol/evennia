"""
Generator-flow menu helpers and input-capturing interaction states.

The menu layer has two halves:

* **Generator-flow helpers** for ``@interactive`` ``carry_out`` rules driven by
  the engine's generator machinery: :class:`MenuPrompt` (+
  :func:`format_menu_prompt` / :func:`parse_menu_choice`) for numbered menus,
  :func:`confirm` for a yes/no sub-flow, and :func:`paginate` for slicing long
  lists. A flow ``yield``\\s these and resumes with the player's choice
  (``resp = yield "prompt"`` / ``choice = yield MenuPrompt(...)``).

* **Input-capturing states** for the callback-style patterns the cmdset world
  handled with special command instances: :class:`InputCaptureState`,
  :class:`GetInputState` (backing :func:`get_input`), :class:`YesNoState`
  (backing :func:`ask_yes_no`), and :class:`DisambiguationState` (installed when
  the parser raises :class:`AmbiguousTarget`).

The states work purely through the rule engine: a catch-all ``before`` rule at
priority 9999 (ahead of any normal rule) fires first and seizes the input. The
states sit at the front of the provider list (``actor.state_objects``), so their
rules win. :class:`MenuInputAction` is the internal system action that captured
raw input is redirected into (``__menuinput__``, excluded from the verb trie).
"""

from dataclasses import dataclass, field

from .action import Action, action
from .result import CLAIM, PASS, REDIRECT, SILENT_FAIL
from .rule import rule
from .state import StateProvider, capture_holder, enter_state, exit_state

__all__ = [
    "MenuInputAction",
    "MenuPrompt",
    "InputCaptureState",
    "GetInputState",
    "YesNoState",
    "DisambiguationState",
    "format_menu_prompt",
    "parse_menu_choice",
    "session_mismatch",
    "confirm",
    "paginate",
    "get_input",
    "ask_yes_no",
]


@action("__menuinput__")
@dataclass
class MenuInputAction(Action):
    """Carries a raw input line into the menu that captured it.

    Attributes:
        raw (str): the line the player typed.
        menu (StateProvider): the capturing input state (e.g. GetInputState).
    """

    raw: str = ""
    menu: object = None


# --------------------------------------------------------------------------- #
# MenuPrompt + InputCaptureState (CM1 @interactive hub menus)
# --------------------------------------------------------------------------- #


@dataclass
class MenuPrompt:
    """Structured yield for branching hub menus in ``@interactive`` rules.

    Attributes:
        text (str): body shown above the option list.
        options (list): ``(key, description)`` pairs; keys may be numeric strings.
        allow_quit (bool): accept ``q`` / ``quit`` to end the flow (returns ``None``).
        allow_look (bool): accept ``l`` / ``look`` to re-show the menu (returns ``"__look__"``).
    """

    text: str
    options: list = field(default_factory=list)
    allow_quit: bool = True
    allow_look: bool = False


def session_mismatch(state_session, actor) -> bool:
    """True when a session-scoped capture should let this actor's input pass.

    Input-capture (a pager, a yes/no, an editor) is logically scoped to the
    *session* the prompt was shown to, even though the state physically lives on
    a body. When ``state_session`` is set, only input from that same session
    drives the capture; lines from another session controlling the same body
    fall through to normal dispatch. A ``None`` ``state_session`` is
    session-agnostic (any session triggers it - the legacy behavior).
    """
    if state_session is None:
        return False
    return state_session is not getattr(actor, "session", None)


_EXIT_KEYS = frozenset({"q", "quit", "exit"})


def _menu_has_exit_option(menu: MenuPrompt) -> bool:
    for key, _desc in menu.options:
        if str(key).lower() in _EXIT_KEYS:
            return True
    return False


def format_menu_prompt(menu: MenuPrompt) -> str:
    """Render option keys and descriptions (EvMenu / matrix formatter style)."""
    lines = [menu.text.rstrip(), ""]
    for key, desc in menu.options:
        label = desc or key
        lines.append(f"  |w{key}|n: {label}")
    if menu.allow_quit and not _menu_has_exit_option(menu):
        lines.append("  |wq|n: Quit")
    if menu.allow_look:
        lines.append("  |wl|n: Look")
    return "\n".join(lines)


def parse_menu_choice(raw, menu: MenuPrompt):
    """Map player input to an option key, ``None`` (quit), or ``"__look__"``."""
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    lowered = token.lower()
    if menu.allow_quit and lowered in ("q", "quit", "exit"):
        return None
    if menu.allow_look and lowered in ("l", "look"):
        return "__look__"
    if token.isdigit():
        idx = int(token)
        if 1 <= idx <= len(menu.options):
            return menu.options[idx - 1][0]
        return "__invalid__"
    for key, _desc in menu.options:
        if str(key).lower() == lowered:
            return key
    return "__invalid__"


# --------------------------------------------------------------------------- #
# Generator-flow combinators (for ``@interactive`` carry_out rules)
# --------------------------------------------------------------------------- #


def confirm(prompt, yes_label="Yes", no_label="No"):
    """Yes/no sub-flow for an ``@interactive`` generator.

    Use as ``ok = yield from confirm("Leave group?")``. Returns ``True`` only on
    the yes option; declining or quitting (``q``) is ``False``. Yields a
    :class:`MenuPrompt`, so it composes inside any flow the engine drives.
    """
    choice = yield MenuPrompt(prompt, options=[("y", yes_label), ("n", no_label)])
    return choice == "y"


def paginate(items, page_size, page=0):
    """Pure slice helper for long lists in a flow.

    Returns ``(page_items, page, total_pages)``; ``page`` is clamped into range.
    Replaces the hand-rolled ``items[:N]`` / ``limit=N`` slicing in menu screens.
    """
    items = list(items)
    total = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total - 1))
    start = page * page_size
    return items[start : start + page_size], page, total


class InputCaptureState(StateProvider):
    """One-shot capture of the next input line for ``@interactive`` suspension.

    Installed by :func:`evennia.actions.engine.request_input`. The player's next
    line is delivered to the waiting ``Deferred`` and the state exits.
    """

    def __init__(self, deferred, session=None):
        self.deferred = deferred
        self.session = session

    @rule(Action, phase="before", priority=9999)
    def capture_input(self, action, actor):
        if isinstance(action, MenuInputAction):
            return PASS
        if session_mismatch(self.session, actor):
            return PASS
        return REDIRECT(MenuInputAction(raw=action._raw_string, menu=self))

    @rule(MenuInputAction, phase="carry_out", priority=9999)
    def deliver_input(self, action, actor):
        if action.menu is not self:
            return PASS
        actor.exit_state(InputCaptureState)
        waiter = self.deferred
        if waiter is not None:
            if hasattr(waiter, "done"):
                if not waiter.done():
                    waiter.set_result(action.raw)
            elif hasattr(waiter, "called") and not waiter.called:
                waiter.callback(action.raw)
        return CLAIM


# --------------------------------------------------------------------------- #
# GetInputState / YesNoState (+ the get_input / ask_yes_no entry points)
# --------------------------------------------------------------------------- #


class GetInputState(StateProvider):
    """Capturing state backing :func:`get_input`.

    Captures the next input line and runs
    ``callback(caller, prompt, result, *args, **kwargs)``. A falsy return ends the
    prompt (the state exits); a truthy return keeps the state active so the
    callback can collect another line (the callback re-prompts as needed). Backs
    the same pattern the legacy ``InputCmdSet`` did, but as a rule provider so the
    action engine routes the captured line.
    """

    def __init__(self, caller, prompt, callback, session=None, args=(), kwargs=None):
        self.caller = caller
        self.prompt = prompt
        self.callback = callback
        self.session = session
        self.args = tuple(args)
        self.kwargs = dict(kwargs or {})

    @rule(Action, phase="before", priority=9999)
    def capture_input(self, action, actor):
        if isinstance(action, MenuInputAction):
            return PASS
        if session_mismatch(self.session, actor):
            return PASS
        return REDIRECT(MenuInputAction(raw=action._raw_string, menu=self))

    @rule(MenuInputAction, phase="carry_out", priority=9999)
    def deliver_input(self, action, actor):
        if action.menu is not self:
            return PASS
        result = (action.raw or "").rstrip()
        try:
            keep = self.callback(self.caller, self.prompt, result, *self.args, **self.kwargs)
        except Exception:
            from evennia.utils import logger

            self.caller.msg("|rError in get_input. Choice not confirmed (report to admin)|n")
            logger.log_trace("Error in get_input")
            actor.exit_state(GetInputState)
            return CLAIM
        if not keep:
            actor.exit_state(GetInputState)
        return CLAIM


class YesNoState(StateProvider):
    """Capturing state backing :func:`ask_yes_no`.

    Captures the next input line and resolves it as a yes/no/abort choice,
    invoking ``yes_callable`` / ``no_callable`` with ``(caller, *args, **kwargs)``
    (``kwargs["caller_session"]`` is set to the answering session). An empty line
    uses ``default``; an unrecognized line re-shows the prompt and keeps the state
    active. Backs the same pattern the legacy ``YesNoQuestionCmdSet`` did.
    """

    def __init__(
        self,
        caller,
        prompt,
        yes_callable,
        no_callable,
        default=None,
        allow_abort=False,
        session=None,
        args=(),
        kwargs=None,
    ):
        self.caller = caller
        self.prompt = prompt
        self.yes_callable = yes_callable
        self.no_callable = no_callable
        self.default = default
        self.allow_abort = allow_abort
        self.session = session
        self.args = tuple(args)
        self.kwargs = dict(kwargs or {})

    @rule(Action, phase="before", priority=9999)
    def capture_input(self, action, actor):
        if isinstance(action, MenuInputAction):
            return PASS
        if session_mismatch(self.session, actor):
            return PASS
        return REDIRECT(MenuInputAction(raw=action._raw_string, menu=self))

    @rule(MenuInputAction, phase="carry_out", priority=9999)
    def deliver_input(self, action, actor):
        if action.menu is not self:
            return PASS
        caller = self.caller
        session = self.session or getattr(actor, "session", None)
        raw = (action.raw or "").strip()
        inp = raw if raw else (self.default or "")
        if isinstance(inp, str):
            inp = inp.lower()
        try:
            if inp in ("a", "abort") and self.allow_abort:
                caller.msg("Aborted.", session=session)
                actor.exit_state(YesNoState)
                return CLAIM
            kwargs = dict(self.kwargs)
            kwargs["caller_session"] = session
            if inp in ("yes", "y"):
                self.yes_callable(caller, *self.args, **kwargs)
            elif inp in ("no", "n"):
                self.no_callable(caller, *self.args, **kwargs)
            else:
                # Unrecognized input: re-show the prompt and keep waiting.
                caller.msg(self.prompt, session=session)
                return CLAIM
            actor.exit_state(YesNoState)
        except Exception:
            # Mirror GetInputState: log the trace (not silent) and notify the
            # answerer, then exit the prompt. Don't re-raise — a buggy callback
            # shouldn't cascade a second untrapped-error message on top.
            from evennia.utils import logger

            caller.msg("|rError in ask_yes_no. Choice not confirmed (report to admin)|n")
            logger.log_trace("Error in ask_yes_no")
            actor.exit_state(YesNoState)
        return CLAIM


def get_input(caller, prompt, callback, session=None, *args, **kwargs):
    """Ask ``caller`` for a line of input and route the reply to ``callback``.

    Args:
        caller (Account or Object): The entity being asked. Usually a
            user-controlled object.
        prompt (str): Shown to the user to indicate input is needed.
        callback (callable): Called as ``callback(caller, prompt, result)`` when
            the user replies. Return falsy (or nothing) to clean up and exit the
            prompt; return True to keep the prompt active and accept another line.
        session (Session, optional): The session to send the prompt to. Usually
            only needed when ``caller`` is an Account in multisession modes > 2.
        *args (any): Extra positional args passed to ``callback``.
        **kwargs (any): Extra keyword args passed to ``callback``.

    Raises:
        RuntimeError: If ``callback`` is not callable.

    Notes:
        The result is raw (it usually keeps the trailing newline from the
        client), so strip before comparing. While running, the prompt is backed
        by a :class:`GetInputState` on the caller's focus body (not an ndb
        attribute or a cmdset); the action engine routes the next input line to
        it. A new ``get_input`` on the same caller replaces any active one
        (exit-before-enter), so prompts do not stack.

    """
    if not callable(callback):
        raise RuntimeError("get_input: input callback is not callable.")
    # Install on the focus body the engine will read for the next line (not the
    # raw caller, which can differ from the focus - e.g. an account caller while
    # a character is puppeted). See capture_holder.
    holder = capture_holder(caller, session)
    # Avoid stacking; the legacy InputCmdSet used Replace for the same reason.
    exit_state(holder, GetInputState)
    enter_state(
        holder,
        GetInputState(caller, prompt, callback, session=session, args=args, kwargs=kwargs),
    )
    caller.msg(prompt, session=session)


def ask_yes_no(
    caller,
    prompt="Yes or No {options}?",
    yes_action="Yes",
    no_action="No",
    default=None,
    allow_abort=False,
    session=None,
    *args,
    **kwargs,
):
    """Ask ``caller`` a simple yes/no question and act on the reply.

    Args:
        caller (Object): The entity being asked.
        prompt (str): The question. An optional ``{options}`` marker is filled
            with 'Y/N', '[Y]/N' or 'Y/[N]' per ``default`` (plus '/Abort' or
            '/[A]bort' when ``allow_abort`` is set).
        yes_action (callable or str): If callable, called as
            ``yes_action(caller, *args, **kwargs)`` on a Yes; if a string, that
            string is echoed back.
        no_action (callable or str): As ``yes_action`` but for a No.
        default (str, optional): Used when the user just presses return. One of
            'N', 'Y', 'A' or ``None`` (an explicit choice is required). 'A'
            implies ``allow_abort``.
        allow_abort (bool, optional): If set, an 'A(bort)' option exits the
            prompt without choosing yes or no.
        session (Session, optional): The session to send the prompt to. Usually
            only needed when ``caller`` is an Account in multisession modes > 2.
            The answering session is passed to callbacks as
            ``kwargs["caller_session"]``.
        *args: Additional args passed into the callables.
        **kwargs: Additional keyword args passed into the callables.

    Example:
        ::

            # echo strings
            ask_yes_no(caller, "Are you happy {options}?",
                       "you answered yes", "you answered no")
            # trigger callables
            ask_yes_no(caller, "Are you sad {options}?",
                       _callable_yes, _callable_no, allow_abort=True)

    """

    def _callable_yes_txt(caller, *args, **kwargs):
        caller.msg(kwargs["yes_txt"], session=kwargs["caller_session"])

    def _callable_no_txt(caller, *args, **kwargs):
        caller.msg(kwargs["no_txt"], session=kwargs["caller_session"])

    if not callable(yes_action):
        kwargs["yes_txt"] = str(yes_action)
        yes_action = _callable_yes_txt

    if not callable(no_action):
        kwargs["no_txt"] = str(no_action)
        no_action = _callable_no_txt

    # prepare the prompt with options
    options = "Y/N"
    abort_txt = "/Abort" if allow_abort else ""
    if default:
        default = default.lower()
        if default == "y":
            options = "[Y]/N"
        elif default == "n":
            options = "Y/[N]"
        elif default == "a":
            allow_abort = True
            abort_txt = "/[A]bort"
    options += abort_txt
    prompt = prompt.format(options=options)

    # Install on the focus body the engine will read for the next line; see
    # capture_holder (and get_input above) for why the raw caller is wrong.
    holder = capture_holder(caller, session)
    # Avoid stacking; the legacy YesNoQuestionCmdSet used Replace for the same reason.
    exit_state(holder, YesNoState)
    enter_state(
        holder,
        YesNoState(
            caller,
            prompt,
            yes_action,
            no_action,
            default=default,
            allow_abort=allow_abort,
            session=session,
            args=args,
            kwargs=kwargs,
        ),
    )
    caller.msg(prompt, session=session)


# --------------------------------------------------------------------------- #
# DisambiguationState
# --------------------------------------------------------------------------- #


def _candidate_label(candidate, looker=None):
    """Viewer-aware display name for a search candidate.

    When ``looker`` is given, uses ``candidate.get_display_name(looker)`` so the
    game's sdesc/recog rules apply — a character's real ``key`` is never leaked
    into a disambiguation prompt (you see "a tall man in a trenchcoat", not the
    object name). Falls back to ``key``/``name`` only when there is no looker
    (programmatic callers, or non-character candidates without display names).
    """
    if looker is not None:
        getter = getattr(candidate, "get_display_name", None)
        if callable(getter):
            try:
                name = getter(looker)
                if name:
                    return str(name)
            except Exception:
                pass
    for attr in ("key", "name"):
        val = getattr(candidate, attr, None)
        if val:
            return str(val)
    return str(candidate)


def _parse_choice(raw, candidates, looker=None):
    """Resolve ``raw`` to one of ``candidates`` (1-based index, else name).

    Name matching uses the same viewer-aware :func:`_candidate_label` as the
    prompt, so a player picks by the sdesc/recog name they were shown — never by
    the hidden object key. Returns the chosen candidate, or ``None`` for an
    unrecognized choice.
    """
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    if token.isdigit():
        idx = int(token)
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
        return None
    lowered = token.lower()
    for cand in candidates:
        if _candidate_label(cand, looker).lower() == lowered:
            return cand
    return None


class DisambiguationState(StateProvider):
    """One-shot state that resolves an ambiguous target on the next input.

    Installed by the dispatch loop (Phase 4) when the parser raises
    :class:`AmbiguousTarget`. The next input line is read as a choice; a valid
    choice patches the pending action's target field and re-dispatches it, an
    invalid one cancels. Either way the state exits.
    """

    def __init__(
        self,
        candidates,
        pending_action=None,
        target_field="target",
        pending_raw=None,
        ambiguous_name=None,
    ):
        self.candidates = list(candidates)
        # Two resolution styles:
        #   * pending_action — patch its target field and REDIRECT (engine-native;
        #     the resolve_disambiguation before-rule below).
        #   * pending_raw + ambiguous_name — the cmdhandler bridge resolves the
        #     choice itself, stashes a search override, and replays pending_raw
        #     (used when AmbiguousTarget is raised mid-parse, before any action
        #     object exists). See evennia.actions.dispatch.
        self.pending = pending_action
        self.target_field = target_field
        self.pending_raw = pending_raw
        self.ambiguous_name = ambiguous_name

    def prompt(self, looker=None):
        """The disambiguation menu text ("1-ball, 2-ball, …").

        ``looker`` (the choosing character) makes the candidate labels
        viewer-aware (sdesc/recog), so a character's real key is never shown.
        """
        lines = [f"  {i + 1}: {_candidate_label(c, looker)}" for i, c in enumerate(self.candidates)]
        return "Which one did you mean?\n" + "\n".join(lines)

    @rule(Action, phase="before", priority=9999)
    def resolve_disambiguation(self, action, actor):
        """Read the next input as a choice; redirect the pending action or cancel.

        The dispatch loop reuses the same provider list across a ``REDIRECT``, so
        this rule stays in the context for the very action it redirects to. Once
        the state has exited (below), ``has_state`` is False and we let the
        redirected pending action through untouched.
        """
        if not actor.has_state(DisambiguationState):
            return PASS
        if self.pending is None:
            # pending_raw style — the cmdhandler bridge resolves this before the
            # input ever reaches the engine, so there's nothing to do here.
            return PASS
        choice = _parse_choice(
            action._raw_string, self.candidates, looker=getattr(actor, "character", None)
        )
        if choice is None:
            actor.msg("Invalid choice. Cancelled.")
            actor.exit_state(DisambiguationState)
            return SILENT_FAIL
        setattr(self.pending, self.target_field, choice)
        actor.exit_state(DisambiguationState)
        return REDIRECT(self.pending)
