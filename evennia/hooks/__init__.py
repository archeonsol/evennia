"""Engine hook registry.

Public API for declaring and introspecting engine hooks. The registry
is descriptive metadata, not a dispatcher: the engine continues to
call hooks directly. See ``docs/source/Components/Typeclass-Hooks.md``
for the contract this surface validates, and the H1 entry in
``.agents/docs/engine-api-architecture.md`` for the design.
"""

from .registry import describe, for_event, hook, lint, list_all
from .specs import HookSpec, LintFinding

__all__ = [
    "HookSpec",
    "LintFinding",
    "describe",
    "for_event",
    "hook",
    "lint",
    "list_all",
]
