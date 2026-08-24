"""
The ``Action`` base + ``@action`` decorator (CM1 Phase 0b).

An action is a typed, dataclass-shaped request ("kick the ball hard") that the
parser produces and the rule engine dispatches. Verbs are registered globally at
import time via ``@action(*verbs)``; a duplicate verb is an import-time error.

Subclasses are plain dataclasses::

    @action("kick")
    @dataclass
    class Kick(Action):
        __primary_handler__ = Ball
        target: object
        strength: str = "soft"

The base carries four private, ``init=False`` fields: three the engine fills in
(`_actor`, `_raw_string`, `_trace`) plus `_unresolved`, which a ``parse`` sets
when a required target resolved to nothing. They are ``init=False`` so a subclass
may declare required fields (no default) without tripping the dataclass
"non-default-after-default" ordering rule.
"""

from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import ClassVar, Literal, Optional, get_args, get_origin, get_type_hints

from .exceptions import ParseError
from .registry import action_registry
from .result import FAIL

__all__ = ["Action", "action", "GameObject"]


class GameObject:
    """Parse-time marker for an Action field that resolves to a world object.

    A field annotated ``target: GameObject`` tells the default
    :meth:`Action.parse` to resolve the corresponding token via
    ``actor.search(token)`` (which raises :class:`AmbiguousTarget` on multiple
    matches and returns ``None`` on no match). The *runtime* value stored on the
    action is whatever ``search`` returns — a real typeclassed object — not a
    ``GameObject`` instance; this class is never instantiated.
    """

    __slots__ = ()


def _literal_options(annotation):
    """Return the tuple of allowed values if ``annotation`` is a ``Literal``,
    else ``None``."""
    if get_origin(annotation) is Literal:
        return get_args(annotation)
    return None


def _is_gameobject(annotation) -> bool:
    """True if ``annotation`` is the :class:`GameObject` marker (or a subclass)."""
    return annotation is GameObject or (
        isinstance(annotation, type) and issubclass(annotation, GameObject)
    )


def _has_default(f) -> bool:
    """True if dataclass field ``f`` carries a default (value or factory)."""
    from dataclasses import MISSING

    return f.default is not MISSING or f.default_factory is not MISSING


@dataclass
class Action:
    """Base for all typed actions. Also the catch-all action type for rules that
    respond to *every* action (state gates, stealth reveal): ``@rule(Action,
    ...)``."""

    # Marker (own-dict only) so the registry recognizes the catch-all base
    # without a circular import. Subclasses do not inherit it in their __dict__.
    _is_catch_all_base: ClassVar[bool] = True

    __primary_handler__: ClassVar[Optional[type]] = None

    _actor: object = field(default=None, init=False, repr=False, compare=False)
    _raw_string: str = field(default="", init=False, repr=False, compare=False)
    _trace: object = field(default=None, init=False, repr=False, compare=False)
    #: Set True by a ``parse`` that resolved a *required* target to nothing — the
    #: miss is already reported to the actor by ``search`` at parse time. The
    #: engine then **skips the check phase** for this action so gate rules never
    #: evaluate a predicate against a ``None`` target (e.g. ``action.target.db.x``
    #: would raise ``AttributeError``); the owning ``carry_out`` rule swallows it
    #: quietly. This keeps an unresolved target a *structural* dead-end rather than
    #: a token every rule author must defensively guard.
    _unresolved: bool = field(default=False, init=False, repr=False, compare=False)
    #: Bitmask a ``check`` rule ORs in via :meth:`block` to name why the action
    #: was refused. The engine treats this as an opaque int; each action family
    #: defines its own ``IntFlag`` vocabulary (e.g. ``MoveBlock``).
    block_reason: int = 0

    def block(self, reason: int = 0, message: str = ""):
        """For a ``check`` rule: OR in a typed reason and fail with ``message``."""
        self.block_reason |= int(reason)
        return FAIL(message)

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        """Build an instance of this action from raw argument text.

        The default implementation auto-parses ``raw_args`` by inspecting the
        subclass's dataclass fields (those with ``init=True``) in declaration
        order and assigning tokens positionally:

        * ``GameObject`` field → resolved via ``actor.search(token)``. A missing
          object raises :class:`ParseError`; multiple matches propagate
          :class:`AmbiguousTarget` from ``search`` (never swallowed).
        * ``Literal[...]`` field → the token (or a matching ``switch``) must be
          one of the allowed values, else :class:`ParseError`.
        * ``int`` field → ``int(token)`` (``ParseError`` if non-numeric).
        * ``str`` / anything else → the token. The *last* string field is
          greedy: it consumes all remaining tokens joined by spaces, so a
          trailing free-text argument (a message, a pose) stays intact.

        A required field (no default) with no token left raises
        :class:`ParseError`. Override this classmethod for bespoke syntax.

        Args:
            raw_args (str): everything after the verb (and switches).
            actor: the acting entity; must expose ``search(name)``.
            context: the :class:`ActionContext` (unused by the default parser;
                available to overrides).
            switches (iterable): switch tokens extracted from ``verb/sw1/sw2``.
            verb (str | None): the specific verb/alias that matched (the engine
                analogue of a command's ``cmdstring``); lets an override behave
                differently per alias. ``None`` when parsed outside the parser.

        Returns:
            Action: a populated instance of ``cls``.

        Raises:
            ParseError: malformed or missing required arguments.
            AmbiguousTarget: ``actor.search`` matched multiple objects.
        """
        tokens = raw_args.split() if raw_args else []
        switches = list(switches)
        try:
            hints = get_type_hints(cls)
        except Exception:
            hints = {}

        init_fields = [f for f in dataclass_fields(cls) if f.init and f.name != "block_reason"]
        values = {}
        idx = 0
        last_i = len(init_fields) - 1

        for i, f in enumerate(init_fields):
            annotation = hints.get(f.name, f.type)
            literal_opts = _literal_options(annotation)

            # Literal: a matching switch wins, else the next positional token.
            if literal_opts is not None:
                chosen = None
                for sw in list(switches):
                    if sw in literal_opts:
                        chosen = sw
                        switches.remove(sw)
                        break
                if chosen is None and idx < len(tokens):
                    tok = tokens[idx]
                    idx += 1
                    if tok not in literal_opts:
                        raise ParseError(
                            f"'{tok}' is not a valid option for {f.name} "
                            f"(choose from: {', '.join(map(str, literal_opts))})."
                        )
                    chosen = tok
                if chosen is None:
                    if _has_default(f):
                        continue
                    raise ParseError(
                        f"Missing required {f.name} (one of: {', '.join(map(str, literal_opts))})."
                    )
                values[f.name] = chosen
                continue

            # GameObject: resolve via actor.search.
            if _is_gameobject(annotation):
                if idx >= len(tokens):
                    if _has_default(f):
                        continue
                    raise ParseError(f"{cls._verb_label()} what?")
                tok = tokens[idx]
                idx += 1
                found = actor.search(tok)
                if found is None:
                    raise ParseError(f"You don't see '{tok}' here.")
                values[f.name] = found
                continue

            # int
            if annotation is int:
                if idx >= len(tokens):
                    if _has_default(f):
                        continue
                    raise ParseError(f"Missing a number for {f.name}.")
                tok = tokens[idx]
                idx += 1
                try:
                    values[f.name] = int(tok)
                except ValueError:
                    raise ParseError(f"'{tok}' is not a number.")
                continue

            # str / fallback. The last init field is greedy.
            if idx >= len(tokens):
                if _has_default(f):
                    continue
                raise ParseError(f"Missing required {f.name}.")
            if i == last_i:
                values[f.name] = " ".join(tokens[idx:])
                idx = len(tokens)
            else:
                values[f.name] = tokens[idx]
                idx += 1

        return cls(**values)

    @classmethod
    def _verb_label(cls):
        """A human verb for error messages (first registered verb, else class
        name lowercased)."""
        verbs = getattr(cls, "__action_verbs__", ())
        if verbs:
            return verbs[0].capitalize()
        return cls.__name__

    @classmethod
    def _object_field_names(cls):
        """The names of this action's ``GameObject``-typed fields (cached).

        Used to assemble the dispatch context (resolved targets sit after the
        room in provider order). Computed once per class from the type hints.
        """
        cached = cls.__dict__.get("__evennia_object_fields__")
        if cached is not None:
            return cached
        try:
            hints = get_type_hints(cls)
        except Exception:
            hints = {}
        names = tuple(
            f.name
            for f in dataclass_fields(cls)
            if f.init and _is_gameobject(hints.get(f.name, f.type))
        )
        cls.__evennia_object_fields__ = names
        return names

    @property
    def targets(self):
        """The resolved world objects this action targets (non-None object
        fields), in declaration order — the direct target(s) for the context."""
        out = []
        for name in type(self)._object_field_names():
            val = getattr(self, name, None)
            if val is not None:
                out.append(val)
        return out


def action(*verbs, help_category=None, auto_help=False, glued=()):
    """Class decorator: register an Action subclass under one or more verbs.

    Args:
        *verbs (str): The verb strings (and aliases) that parse to this action.
        help_category (str, optional): Staff command-index category when action
            help is enabled (see ``HELP_INDEX_ACTIONS*`` settings).
        auto_help (bool): If True, include in staff command help when permitted.
            Player-facing prose belongs in file/DB help entries, not docstrings.
        glued (iterable[str]): Aliases that may be joined directly to their
            arguments, such as ``p.wave`` for a ``p.`` alias. Every glued alias
            must also appear in ``verbs``. Punctuation-only aliases already
            support this without opting in.

    Raises:
        ValueError: if no verb is given.
        RuleConflict: if a verb is already bound to a different action.
    """
    if not verbs:
        raise ValueError("@action requires at least one verb")

    glued = tuple(glued or ())
    unknown_glued = {verb.lower() for verb in glued} - {verb.lower() for verb in verbs}
    if unknown_glued:
        raise ValueError(
            "glued action aliases must also be registered verbs: "
            + ", ".join(sorted(unknown_glued))
        )

    def deco(cls):
        cls.__action_verbs__ = tuple(verbs)
        cls.__action_glued_verbs__ = glued
        if help_category is not None:
            cls.help_category = help_category
        cls.auto_help = auto_help
        action_registry.register(cls, verbs)
        return cls

    return deco
