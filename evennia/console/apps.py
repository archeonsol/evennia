"""Django application configuration for the engine console."""

from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    """Load console panels once Django is ready.

    Panel modules are imported here rather than at first request so a
    misconfigured ``CONSOLE_PANEL_MODULES`` fails at startup, matching how
    ``SYSTEM_MODULES`` behaves for the scheduler.
    """

    name = "evennia.console"
    verbose_name = "Evennia Console"

    def ready(self):
        """Register built-in panels, then any game-configured ones."""
        from django.conf import settings

        if not getattr(settings, "CONSOLE_ENABLED", True):
            return
        from evennia.console.panels import register_builtin_panels
        from evennia.console.registry import panel_registry

        register_builtin_panels(panel_registry)
        panel_registry.load_modules()
