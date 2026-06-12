"""
Rule results + dispatch trace (CM1 Phase 0d / 0e).

`RuleResult` is the single value every rule body returns. The phase semantics
(short-circuit, arbitration) are interpreted by the engine (Phase 1) from the
result's ``kind``; the result type itself is a plain frozen record.

`ActionTrace` / `PhaseTrace` make every dispatch fully introspectable — the
engine builds one per dispatch and `actor.explain(...)` returns it (Phase 1e).
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

__all__ = [
    "RuleResult",
    "PASS",
    "SKIP",
    "CLAIM",
    "SILENT_FAIL",
    "FAIL",
    "REDIRECT",
    "PhaseTrace",
    "ActionTrace",
]

ResultKind = Literal["pass", "fail", "skip", "redirect", "claim", "silent_fail"]


@dataclass(frozen=True)
class RuleResult:
    """The value a rule body returns.

    Use the module singletons (`PASS`, `SKIP`, `CLAIM`, `SILENT_FAIL`) and the
    constructors (`FAIL(msg)`, `REDIRECT(action)`) rather than building these
    directly — they read better at call sites and the singletons are shared.
    """

    kind: ResultKind
    message: Optional[str] = None
    # An Action to re-dispatch (before phase only). Untyped to avoid an import
    # cycle with action.py; excluded from eq/hash so REDIRECT results stay
    # comparable/hashable even when the action isn't.
    redirect_to: object = field(default=None, compare=False, hash=False)

    # --- convenience predicates the engine reads ---------------------------
    @property
    def is_pass(self) -> bool:
        return self.kind == "pass"

    @property
    def is_skip(self) -> bool:
        return self.kind == "skip"

    @property
    def is_claim(self) -> bool:
        return self.kind == "claim"

    @property
    def is_redirect(self) -> bool:
        return self.kind == "redirect"

    @property
    def blocks(self) -> bool:
        """True if this result halts the action (with or without a message)."""
        return self.kind in ("fail", "silent_fail")


PASS = RuleResult("pass")
SKIP = RuleResult("skip")
CLAIM = RuleResult("claim")
SILENT_FAIL = RuleResult("silent_fail")


def FAIL(message: str) -> RuleResult:
    """Block the action and send ``message`` to the actor."""
    return RuleResult("fail", message=message)


def REDIRECT(action) -> RuleResult:
    """Replace the current action with ``action`` and re-dispatch (before phase)."""
    return RuleResult("redirect", redirect_to=action)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------
@dataclass
class PhaseTrace:
    """One rule's contribution within a phase."""

    phase: str
    provider_key: str
    provider_dbref: Optional[int]
    rule_name: str
    result: RuleResult
    elapsed_us: int = 0


@dataclass
class ActionTrace:
    """The full record of one dispatch. Built by the engine; returned by
    `actor.explain(...)`."""

    action: object
    actor_key: str
    phases: list = field(default_factory=list)
    outcome: Literal["succeeded", "blocked", "no_rules", "redirected"] = "no_rules"
    block_message: Optional[str] = None
    redirect_count: int = 0
    #: B3 lazy tracing — when False the engine skips building PhaseTrace records
    #: (the production bridge sets this; direct dispatch / ``explain`` keeps it
    #: True so the per-rule trace stays inspectable).
    record_phases: bool = True
    #: count of non-skip rules that fired; the cheap signal ``_final_outcome``
    #: uses so the outcome is correct even when ``record_phases`` is False.
    fired: int = 0
    #: per-phase slices of ``fired`` for the dispatch bridge's fail-closed
    #: feedback: a parsed verb whose dispatch fires neither a ``carry_out`` nor
    #: a ``report`` rule ended in silence and needs default feedback. Kept as
    #: cheap counters so they work with ``record_phases=False``.
    carry_out_fired: int = 0
    report_fired: int = 0
    #: ``carry_out`` rules whose ``requires`` gate failed — distinguishes "no
    #: permitted path" (permission denial) from "no path at all".
    carry_out_gated: int = 0

    def record(self, phase_trace: PhaseTrace) -> None:
        self.phases.append(phase_trace)

    def for_phase(self, phase: str):
        return [pt for pt in self.phases if pt.phase == phase]
