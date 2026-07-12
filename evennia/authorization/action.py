"""Typed-action predicate adapter for the authorization service."""

from __future__ import annotations

from dataclasses import dataclass

from evennia.actions.predicate import Predicate

from .service import authorize


@dataclass(frozen=True, slots=True)
class RequiresAuthorization(Predicate):
    """Require a resource authorization decision in an action rule."""

    access_type: str
    resource_getter: object
    cost = 2

    def __call__(self, action, actor) -> bool:
        """Evaluate and attach the decision for downstream UI/error handling."""

        resource = self.resource_getter(action, actor)
        principal = getattr(actor, "effective", None) or getattr(actor, "character", None) or actor
        decision = authorize(principal, resource, self.access_type, default=False)
        try:
            setattr(action, "authorization_decision", decision)
        except (AttributeError, TypeError):
            pass
        return decision.allowed

    def describe(self) -> str:
        """Return a non-sensitive requirement description."""

        return f"requires authorization to {self.access_type}"
