"""
The ``@rule`` decorator + ``RuleSpec`` (CM1 Phase 0c).

A rule is a method on a world object (target, actor, room, state object) that
responds to a typed action in a given phase. ``@rule`` attaches one or more
``RuleSpec`` records to the method; :func:`evennia.actions.registry.RuleRegistry.collect`
later walks a provider class's MRO and indexes those specs by
``(action_type, phase)``.

Stacking is supported — decorating one method with several ``@rule`` lines (for
several action types or phases) appends to the same spec list::

    @rule(OpenDoor, phase="check", requires=Builder)
    @rule(CloseDoor, phase="check", requires=Builder)
    def staff_only(self, action, actor): ...

`requires` is compiled to a :class:`~evennia.actions.predicate.Predicate` **once,
here at decoration time** (never re-parsed at dispatch). It accepts a Predicate,
a Capability, or a bare callable; a lock *string* is rejected (use
``from_lockstring``).
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from .predicate import Predicate, coerce_predicate

__all__ = ["rule", "RuleSpec", "PHASES"]

#: The four phases, in dispatch order.
PHASES = ("before", "check", "carry_out", "report")


@dataclass(slots=True)
class RuleSpec:
    """One registered rule: a method bound to a single action type + phase."""

    action_type: type
    phase: str
    func: Callable
    rule_name: str
    priority: int = 0
    actor_type: Optional[type] = None
    requires: Optional[Predicate] = None
    relay: bool = True
    help_category: Optional[str] = None


def rule(
    action_type,
    *,
    phase: str = "carry_out",
    priority: int = 0,
    actor_type: Optional[type] = None,
    requires=None,
    relay: bool = True,
    help_category: Optional[str] = None,
):
    """Decorate a provider method as a rule.

    Args:
        action_type (type | tuple[type, ...]): The Action subclass(es) this rule
            responds to. Pass the base ``Action`` for a catch-all (e.g. a state
            gate that blocks everything).
        phase (str): One of ``before | check | carry_out | report``.
        priority (int): Within-phase ordering; higher fires first.
        actor_type (type | None): Restrict to a given actor kind (Character /
            Account); ``None`` = any.
        requires: A Predicate / Capability / callable gating whether the body
            runs (False → SKIP). Compiled once here. Lock strings raise TypeError.
        relay (bool): Whether multipuppet relay is allowed for this rule.
        help_category (str | None): For the help availability index.
    """
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}; must be one of {PHASES}")
    action_types = action_type if isinstance(action_type, tuple) else (action_type,)
    compiled = coerce_predicate(requires) if requires is not None else None

    def deco(fn):
        specs = getattr(fn, "__evennia_rule_specs__", None)
        if specs is None:
            specs = []
            fn.__evennia_rule_specs__ = specs
        for at in action_types:
            specs.append(
                RuleSpec(
                    action_type=at,
                    phase=phase,
                    func=fn,
                    rule_name=fn.__name__,
                    priority=priority,
                    actor_type=actor_type,
                    requires=compiled,
                    relay=relay,
                    help_category=help_category,
                )
            )
        return fn

    return deco
