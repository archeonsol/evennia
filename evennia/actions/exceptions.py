"""
Action-system exceptions (CM1 Phase 0).

Kept dependency-free so every other module in the package can import these
without import cycles.
"""

__all__ = [
    "ActionError",
    "RuleConflict",
    "ParseError",
    "AmbiguousTarget",
]


class ActionError(Exception):
    """Base class for all action-system errors (e.g. redirect-loop breach)."""


class RuleConflict(ActionError):
    """Raised at import time when two actions claim the same verb, or when rule
    registration is otherwise inconsistent."""


class ParseError(ActionError):
    """Raised by an action's ``parse`` when the raw input cannot be turned into a
    valid action (bad syntax, missing/invalid target). The dispatch loop turns
    this into a no-match style message to the actor."""

    def __init__(self, message, *, suggestions=None):
        super().__init__(message)
        self.message = message
        self.suggestions = list(suggestions or [])


class AmbiguousTarget(ActionError):
    """Raised by ``actor.search()`` / an action's ``parse`` when a target string
    matches multiple candidates. The dispatch loop catches it, installs a
    ``DisambiguationState``, and prompts the actor — instead of the legacy
    inline "1-ball or 2-ball?" print-and-return-None behavior."""

    def __init__(self, candidates, original_raw=""):
        self.candidates = list(candidates)
        self.original_raw = original_raw
        super().__init__(f"ambiguous target {original_raw!r}: {len(self.candidates)} candidates")
