"""Dataclasses for hook registry entries.

`HookSpec` is the metadata attached to every engine hook by the
`@hook` decorator. `LintFinding` is the lint-result shape consumed by
`hooks.lint()`.
"""

from dataclasses import dataclass, field
from typing import Optional

_PHASES = {"pre", "post", "composite", "failed"}
_RETURNS = {"veto", "transform", "content", "ignored", "conditional"}
_DISCIPLINES = {"public", "internal", "mixed"}
_CACHE_STATES = {"fresh", "rehydrated", "n/a"}


@dataclass(frozen=True)
class HookSpec:
    """Descriptive metadata for one engine hook.

    Attributes:
        event: Canonical event family (e.g. ``"puppet"``, ``"move"``).
            ``None`` only on override-only registrations that use
            ``extends=...``.
        phase: One of ``pre``, ``post``, ``composite``, ``failed``.
        actor: Which actor the hook fires on (``self``, ``mover``,
            ``source``, ``destination``, ``target``, ...).
        returns: Return contract (``veto``, ``transform``, ``content``,
            ``ignored``, ``conditional``).
        discipline: ``public``, ``internal``, or ``mixed``.
        fires_from: Tuple of ``"Class.method"`` call sites that fire
            this hook. Lint resolves each to a real callable.
        state_pk: Whether the object has a database PK at firing.
        state_db_row: Whether a database row exists at firing.
        state_init_done: Whether ``at_init`` has run by firing.
        state_location_set: Whether ``self.location`` is set at firing.
        state_cache_state: ``fresh``, ``rehydrated``, or ``n/a``.
        state_mid_transaction: Whether the hook fires inside a DB
            transaction.
        notes: Free-form notes that belong with the hook (edge cases,
            reattach kwargs, etc.).
        extends: When non-``None``, marks this as an override-only
            registration that records deltas against the named parent
            hook (``"LifecycleMixin.at_pre_puppet"`` style).
    """

    event: Optional[str] = None
    phase: Optional[str] = None
    actor: Optional[str] = None
    returns: Optional[str] = None
    discipline: Optional[str] = None
    fires_from: tuple = field(default_factory=tuple)
    state_pk: Optional[bool] = None
    state_db_row: Optional[bool] = None
    state_init_done: Optional[bool] = None
    state_location_set: Optional[bool] = None
    state_cache_state: Optional[str] = None
    state_mid_transaction: Optional[bool] = None
    notes: Optional[str] = None
    extends: Optional[str] = None

    def __post_init__(self):
        if self.phase is not None and self.phase not in _PHASES:
            raise ValueError(f"phase must be one of {_PHASES}, got {self.phase!r}")
        if self.returns is not None and self.returns not in _RETURNS:
            raise ValueError(f"returns must be one of {_RETURNS}, got {self.returns!r}")
        if self.discipline is not None and self.discipline not in _DISCIPLINES:
            raise ValueError(f"discipline must be one of {_DISCIPLINES}, got {self.discipline!r}")
        if self.state_cache_state is not None and self.state_cache_state not in _CACHE_STATES:
            raise ValueError(
                f"state_cache_state must be one of {_CACHE_STATES}, got {self.state_cache_state!r}"
            )


@dataclass(frozen=True)
class LintFinding:
    """One issue surfaced by ``hooks.lint()``.

    Attributes:
        code: Short machine-readable code (``MISSING_DECORATOR``,
            ``UNRESOLVED_FIRES_FROM``, ...).
        message: Human-readable description.
        location: ``"Class.method"`` or file path the finding refers
            to.
    """

    code: str
    message: str
    location: str
