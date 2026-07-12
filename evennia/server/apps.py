"""Django application configuration for engine server facilities."""

from django.apps import AppConfig


class ServerConfig(AppConfig):
    """Validate extensible engine registries during Django startup."""

    name = "evennia.server"
    label = "server"

    def ready(self):
        """Load authorization definitions and fail on invalid references."""

        from evennia.authorization.capabilities import capability_registry
        from evennia.authorization.policy import load_policy_modules

        capability_registry.load_modules()
        load_policy_modules()
