"""Panels the engine ships.

Registered at app-ready time, before any game-configured module in
``CONSOLE_PANEL_MODULES``, so a game panel can never shadow a built-in one by
accident: the registry refuses a conflicting key rather than silently
replacing it.
"""

from evennia.console.panels.attributes import AttributesPanel
from evennia.console.panels.health import HealthPanel
from evennia.console.panels.migrations import MigrationsPanel
from evennia.console.panels.records import RecordsPanel
from evennia.console.panels.settings import SettingsPanel

#: Built-in panels, in the order they should appear in the nav.
BUILTIN_PANELS = (
    RecordsPanel,
    AttributesPanel,
    MigrationsPanel,
    SettingsPanel,
    HealthPanel,
)

__all__ = (
    "AttributesPanel",
    "BUILTIN_PANELS",
    "HealthPanel",
    "MigrationsPanel",
    "RecordsPanel",
    "SettingsPanel",
    "register_builtin_panels",
)


def register_builtin_panels(registry) -> None:
    """Register every built-in panel onto one registry.

    Args:
        registry: The :class:`~evennia.console.registry.PanelRegistry` to fill.
    """

    for panel in BUILTIN_PANELS:
        registry.register(panel)
