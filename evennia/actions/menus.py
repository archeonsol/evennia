"""
Interaction states (CM1 Phase 3e / Gate 4): the two engine-level states that
intercept the *next* line of input.

Both back patterns the cmdset world handled with special command instances:

* :class:`EvMenuState` — captures every input line and routes it to a menu node,
  the action-engine equivalent of the cmdset-based ``EvMenu``. New linear flows
  should prefer an ``@interactive`` ``carry_out`` rule (``resp = yield "prompt"``,
  driven by the engine's generator machinery — see Phase 1g); this state exists
  so the legacy node-graph API keeps working unchanged.
* :class:`DisambiguationState` — installed when the parser raises
  :class:`AmbiguousTarget`. Its high-priority ``before`` rule reads the next line
  as a choice, patches the pending action's target, and ``REDIRECT``s it.

Both work purely through the rule engine: a catch-all ``before`` rule at priority
9999 (ahead of any normal rule) fires first and seizes the input. The states sit
at the front of the provider list (``actor.state_objects``), so their rules win.

``MenuInputAction`` is the internal action ``EvMenuState`` redirects raw input
into; it is a system action (``__menuinput__``), excluded from the verb trie.
"""

from dataclasses import dataclass, field

from .action import Action, action
from .result import CLAIM, PASS, REDIRECT, SILENT_FAIL
from .rule import rule
from .state import StateProvider

__all__ = [
    "MenuInputAction",
    "MenuPrompt",
    "InputCaptureState",
    "GetInputState",
    "YesNoState",
    "EvMenuState",
    "DisambiguationState",
    "format_menu_prompt",
    "parse_menu_choice",
    "session_mismatch",
]


@action("__menuinput__")
@dataclass
class MenuInputAction(Action):
    """Carries a raw input line into the menu that captured it.

    Attributes:
        raw (str): the line the player typed.
        menu (EvMenuState): the capturing menu state.
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
        d = self.deferred
        if d is not None and not d.called:
            d.callback(action.raw)
        return CLAIM


# --------------------------------------------------------------------------- #
# GetInputState / YesNoState (engine-native evmenu.get_input / ask_yes_no)
# --------------------------------------------------------------------------- #


class GetInputState(StateProvider):
    """Engine-native replacement for :func:`evennia.utils.evmenu.get_input`.

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
    """Engine-native replacement for :func:`evennia.utils.evmenu.ask_yes_no`.

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


# --------------------------------------------------------------------------- #
# EvMenuState
# --------------------------------------------------------------------------- #


class EvMenuState(StateProvider):
    """Captures all input and routes it through a menu node graph.

    The node graph is a ``{node_name: callable}`` map. A node callable receives
    ``(actor, raw_input)`` and returns the *next* node name, or ``None`` to end
    the menu (which exits the state). This is a deliberately small routing model;
    the full legacy ``EvMenu(caller, menudata)`` constructor is preserved as a
    thin wrapper that installs an ``EvMenuState`` (wired in Phase 4).
    """

    def __init__(self, menutree=None, startnode="start", **kwargs):
        self.menutree = dict(menutree or {})
        self.current_node = startnode
        self.kwargs = kwargs

    @rule(Action, phase="before", priority=9999)
    def capture_input(self, action, actor):
        """Seize the next input line — unless it's the MenuInputAction we just
        produced (which must fall through to :meth:`run_node`)."""
        if isinstance(action, MenuInputAction):
            return PASS
        return REDIRECT(MenuInputAction(raw=action._raw_string, menu=self))

    @rule(MenuInputAction, phase="carry_out", priority=9999)
    def run_node(self, action, actor):
        """Execute the current node with the captured input and claim."""
        self.route(action.raw, actor)
        return CLAIM

    def route(self, raw, actor):
        """Run the current node callable and advance (or exit on ``None``)."""
        node = self.menutree.get(self.current_node)
        if node is None:
            actor.exit_state(EvMenuState)
            return
        nxt = node(actor, raw)
        if nxt is None:
            actor.exit_state(EvMenuState)
        else:
            self.current_node = nxt


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
