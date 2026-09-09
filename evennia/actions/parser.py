"""
The action parser (CM1 Phase 2): raw command text → a typed :class:`Action`.

The parser is deliberately thin. It does three things:

1. Split the input into a verb (with optional ``/switches``) and the remaining
   argument text.
2. Resolve the verb against the registry (longest exact multi-word phrase, then
   single-token :class:`~evennia.actions.registry.VerbTrie` prefix), producing a
   *confidence* score.
3. Hand the argument text to the matched action's ``parse`` classmethod, which
   turns it into a populated dataclass instance.

Failure modes map onto system actions rather than ``None``-returns or printed
errors:

* Empty input → :meth:`ActionParser.parse` returns ``None`` (the dispatch bridge
  in Phase 4 substitutes :class:`NoInputAction`).
* Unknown / ambiguous verb → :class:`NoMatchAction` carrying the raw string and a
  list of fuzzy ``suggestions``.
* A matched action whose ``parse`` raises :class:`ParseError` → also
  :class:`NoMatchAction`, carrying ``parse``'s message.

:class:`AmbiguousTarget` (raised by ``actor.search`` on a multi-match) is **not**
swallowed: it propagates out of the parser so the Phase 4 dispatch loop can stand
up a disambiguation state. That is the whole point of the parser refusing to
"print and return None" the way the legacy command system did.
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from .action import Action, action
from .exceptions import AmbiguousTarget, ParseError
from .registry import action_registry

__all__ = [
    "ParseResult",
    "ActionParser",
    "parser",
    "NoInputAction",
    "NoMatchAction",
    "LoginStartAction",
    "DynamicVerbResolver",
]


@runtime_checkable
class DynamicVerbResolver(Protocol):
    """A resolver for verbs that cannot live in the static :class:`VerbTrie`.

    Some "verbs" are *contextual*: room exit names ("north", "shard", "fire
    escape"), channel aliases, per-character nicks. They are unknown at import
    time, so the registry can't hold them. A resolver is consulted by
    :meth:`ActionParser.parse` only **after** the trie and symbol-prefix lookups
    both miss — so a statically registered verb always wins — and **before**
    falling through to :class:`NoMatchAction`.

    A resolver is any callable ``(stripped, actor) -> Action | None``:

    * return a populated :class:`Action` to claim the input;
    * return ``None`` to decline (the next resolver, then ``_nomatch``, is tried);
    * raise :class:`AmbiguousTarget` to trigger disambiguation (e.g. two exits
      both named "door"). Since no action exists yet, pass a ``choice_resolver``
      that turns the selected candidate into the action to dispatch.

    Resolvers keep the engine game-agnostic: the *exit* resolver lives game-side
    and is registered onto the shared parser when the movement package imports,
    exactly as ``@action`` verbs register at import time.
    """

    def __call__(self, stripped: str, actor) -> Optional[Action]: ...


@dataclass
class ParseResult:
    """The outcome of parsing one line of input.

    Attributes:
        action (Action): the typed action ready for the engine to dispatch.
        raw_verb (str): the verb token exactly as the player typed it (before
            trie resolution; useful for echoing abbreviations).
        raw_args (str): everything after the verb and switches.
        confidence (float): ``1.0`` for an exact verb match, ``< 1.0`` for a
            prefix match (``len(raw_verb) / len(canonical_verb)``), and ``0.0``
            for a no-match (the action is a :class:`NoMatchAction`).
    """

    action: Action
    raw_verb: str
    raw_args: str
    confidence: float = 1.0


# --------------------------------------------------------------------------- #
# System actions
# --------------------------------------------------------------------------- #


@action("__noinput__")
@dataclass
class NoInputAction(Action):
    """Dispatched when the player submits an empty line."""


@action("__nomatch__")
@dataclass
class NoMatchAction(Action):
    """Dispatched when no verb matches (or a matched action failed to parse).

    Attributes:
        raw_string (str): the full line the player typed.
        suggestions (list): fuzzy near-miss verbs ("did you mean…").
        error (str): a parse error message, when the verb matched but its
            arguments were malformed.
    """

    raw_string: str = ""
    suggestions: list = field(default_factory=list)
    error: str = ""


@action("__loginstart__")
@dataclass
class LoginStartAction(Action):
    """Dispatched once when a session connects, before any input."""


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


class ActionParser:
    """Turns raw input text into a :class:`ParseResult`. Stateless; one shared
    instance (:data:`parser`) is fine, but tests may build their own."""

    def __init__(self, registry=None, resolvers=None):
        self._registry = registry or action_registry
        self._resolvers = list(resolvers or [])

    def add_resolver(self, resolver):
        """Append a :class:`DynamicVerbResolver` to the trie-miss chain.

        Resolvers are consulted in registration order. Idempotent for the same
        callable object (re-importing the movement package won't double-register
        on the shared parser singleton)."""
        if resolver not in self._resolvers:
            self._resolvers.append(resolver)
        return resolver

    def clear_resolvers(self):
        """Drop all dynamic resolvers (mainly for tests)."""
        self._resolvers.clear()

    def _resolve_dynamic(self, stripped, actor):
        """Try each resolver in turn; return the first non-``None`` Action.

        :class:`AmbiguousTarget` from a resolver propagates (the dispatch bridge
        turns it into a disambiguation prompt), exactly like ``actor.search``.
        """
        if actor is None:
            return None
        for resolver in self._resolvers:
            built = resolver(stripped, actor)
            if built is not None:
                return built
        return None

    @staticmethod
    def _split_verb(verb_token):
        """Split a ``verb/switch1/switch2`` token into ``(verb, [switches])``."""
        if "/" not in verb_token:
            return verb_token, []
        parts = verb_token.split("/")
        verb = parts[0]
        switches = [p for p in parts[1:] if p]
        return verb, switches

    @classmethod
    def _split_candidate_tokens(cls, tokens):
        """Return verb-match tokens with any inline switches separated.

        Slash switches may appear on any word of a multi-word verb
        (``deck deal/faceup``). All tokens are normalized for matching, but
        switches are only consumed from the final matched verb span so slashes
        in arguments remain ordinary argument text.
        """
        match_tokens = []
        token_switches = []
        for token in tokens:
            verb_part, switches = cls._split_verb(token)
            match_tokens.append(verb_part)
            token_switches.append(switches)
        return match_tokens, token_switches

    def _match_symbol_prefix(self, stripped):
        """Match an eligible no-space prefix verb glued to its arguments.

        Tries all-punctuation verbs plus aliases that explicitly opted into
        glued matching, longest-first. Switches are not supported in the glued
        form.

        Returns ``(verb, switches, match, raw_args)`` or ``None``.
        """
        for sym in self._registry.no_space_prefix_verbs:
            if stripped.startswith(sym):
                match = self._registry.match_verb(sym)
                if match is None:
                    continue
                return sym, [], match, stripped[len(sym) :].strip()
        return None

    def parse(self, raw_string, actor, context=None):
        """Parse ``raw_string`` into a :class:`ParseResult`, or ``None`` if the
        input is empty.

        Args:
            raw_string (str): the player's raw input line.
            actor: the acting entity (must expose ``search`` for object args).
            context: the :class:`ActionContext` for this dispatch (passed
                through to per-action ``parse``).

        Returns:
            ParseResult | None: ``None`` only for empty input.

        Raises:
            AmbiguousTarget: propagated from ``actor.search`` (never swallowed).
        """
        if raw_string is None:
            return None
        stripped = raw_string.strip()
        if not stripped:
            return None

        raw_tokens = stripped.split()
        tokens, token_switches = self._split_candidate_tokens(raw_tokens)
        verb = tokens[0]

        vm = self._registry.match_tokens(tokens)
        # Single-char lines are often compass exit aliases; prefer a dynamic
        # resolver (exit names) over a weak verb prefix (e.g. ``n`` → ``newrecipe``).
        if vm is not None and vm.span == 1 and len(tokens[0]) == 1:
            resolved = self._resolve_dynamic(stripped, actor)
            if resolved is not None:
                resolved._raw_string = raw_string
                return ParseResult(
                    action=resolved,
                    raw_verb=verb,
                    raw_args=" ".join(tokens[1:]),
                    confidence=1.0,
                )
        if vm is not None:
            raw_verb = " ".join(tokens[: vm.span])
            raw_args = " ".join(raw_tokens[vm.span :])
            switches = [switch for switches in token_switches[: vm.span] for switch in switches]
            canonical = vm.canonical
            action_cls = vm.action_cls
            confidence = vm.confidence
        else:
            # No registered verb matched. Try an eligible no-space prefix
            # (``.wave``, ``"hi``, ``p.wave``) — the engine analogue of a
            # command's ``arg_regex=None``. Word-bearing forms must opt in.
            symbol = self._match_symbol_prefix(stripped)
            if symbol is None:
                # Last chance before no-match: dynamic verbs (exit names, …).
                # A resolver claims the *whole* stripped line and returns a built
                # action directly — there is no separate ``parse`` step for it.
                resolved = self._resolve_dynamic(stripped, actor)
                if resolved is not None:
                    resolved._raw_string = raw_string
                    return ParseResult(
                        action=resolved,
                        raw_verb=verb,
                        raw_args=" ".join(tokens[1:]),
                        confidence=1.0,
                    )
                return self._nomatch(raw_string, verb, actor)
            verb, switches, match, raw_args = symbol
            canonical, action_cls, confidence = match
            raw_verb = verb

        try:
            built = action_cls.parse(raw_args, actor, context, switches=switches, verb=canonical)
        except ParseError as err:
            return self._nomatch(raw_string, verb, actor, error=getattr(err, "message", str(err)))
        # AmbiguousTarget intentionally NOT caught here.

        built._raw_string = raw_string
        return ParseResult(
            action=built,
            raw_verb=raw_verb,
            raw_args=raw_args,
            confidence=confidence,
        )

    def _nomatch(self, raw_string, verb, actor=None, error=""):
        """Build a :class:`NoMatchAction` ParseResult with fuzzy suggestions.

        Suggestions are filtered through :func:`_verb_reachable` so a typo
        never offers back a verb the actor has no non-gated path to — the
        suggestion side of the dispatch bridge's fail-closed boundary (a fully
        permission-gated verb must be indistinguishable from one that does not
        exist).
        """
        reachable = (lambda _v, cls: _verb_reachable(cls, actor)) if actor is not None else None
        suggestions = self._registry.suggest_verbs(verb, reachable=reachable) if verb else []
        nomatch = NoMatchAction(raw_string=raw_string, suggestions=suggestions, error=error)
        nomatch._raw_string = raw_string
        return ParseResult(
            action=nomatch,
            raw_verb=verb,
            raw_args=raw_string,
            confidence=0.0,
        )


def _verb_reachable(action_cls, actor) -> bool:
    """True unless every ``carry_out`` path for ``action_cls`` in ``actor``'s
    provider context is permission-gated against them.

    Mirrors the dispatch bridge's fail-closed boundary
    (:func:`evennia.actions.dispatch._fail_closed_fallback`): a fully
    ``requires``-gated verb dispatches to a no-match, so it must not be offered
    as a suggestion either. A verb with *no* carry_out rules at all is
    inapplicable, not secret (dispatch messages it honestly), so it stays
    suggestible. Gate predicates are evaluated without a parsed action; one
    that needs the action (or raises) does not count as a reachable path, and
    an actor the provider context cannot be built for gets nothing (fail
    closed).

    Args:
        action_cls (type): the candidate :class:`~evennia.actions.action.Action`.
        actor: the asking actor.

    Returns:
        bool: whether the verb may be offered to this actor.
    """
    from .context import build_context
    from .engine import RuleEngine
    from .registry import rule_registry

    try:
        context = build_context(actor, action_type=action_cls)
    except Exception:
        return False
    gated = False
    for provider in context.providers:
        for spec in rule_registry.rules_for(type(provider), action_cls, "carry_out"):
            if not RuleEngine._actor_type_ok(spec, actor):
                continue
            if spec.requires is None:
                return True
            gated = True
            try:
                if spec.requires.eval(None, actor):
                    return True
            except Exception:
                continue
    return not gated


#: shared parser instance
parser = ActionParser()
