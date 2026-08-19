"""
Deprecated alias for :mod:`evennia.eventbus`.

The engine event bus moved to ``evennia.eventbus`` so the top-level
``evennia.events`` name stopped shadowing :mod:`evennia.actions.events`, the
unrelated in-process ``EventRegistry`` that backs ``@subscribe`` on action
handlers. Every ``subscribe`` call site in the tree resolves to that other
module; this one's ``subscribe`` had no consumers at all.

This shim exists for one release so a repo that has not been updated fails
loudly instead of silently. Callers that swallow import errors around
``emit`` -- see the game's ``world/audit/emit.py`` -- would otherwise stop
auditing without raising anything.

Update imports to::

    from evennia.eventbus import emit

This module is removed in the release after the one that introduced it.

"""

import warnings

from evennia.eventbus.bus import emit, subscribe

warnings.warn(
    "evennia.events is deprecated and will be removed in the next release; "
    "import from evennia.eventbus instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ("emit", "subscribe")
