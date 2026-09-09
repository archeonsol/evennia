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
    """Raised when a target string matches multiple candidates.

    Args:
        candidates (iterable): Candidate objects shown to the actor.
        original_raw (str): Ambiguous target text.
        choice_resolver (callable, optional): Convert one selected candidate
            into an action, or return ``None`` when that candidate expired.
            Search-driven ambiguity omits this and uses replay instead.
    """

    def __init__(self, candidates, original_raw="", choice_resolver=None):
        self.candidates = list(candidates)
        self.original_raw = original_raw
        self.choice_resolver = choice_resolver
        super().__init__(f"ambiguous target {original_raw!r}: {len(self.candidates)} candidates")
