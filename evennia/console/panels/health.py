"""Server health as a panel.

Thin wrapper over :mod:`evennia.console.health`, which is also served
unauthenticated-adjacent at ``api/console/health/`` so an external uptime check
consumes the same judgement the console shows rather than inventing its own.
"""

from __future__ import annotations

from evennia.console import health
from evennia.console.registry import Panel


class HealthPanel(Panel):
    """One page that answers whether the server is well."""

    key = "health"
    label = "Health"
    description = "Green or not green per check, with the underlying value."
    columns = ("check", "ok")
    needs_io = False

    def rows(self, ctx):
        """Return the health summary plus a row per check."""
        state = health.status()
        return {
            "healthy": state["healthy"],
            "degraded": state["degraded"],
            "version": state["version"],
            "rows": [{"check": name, "ok": ok} for name, ok in sorted(state["checks"].items())],
        }
