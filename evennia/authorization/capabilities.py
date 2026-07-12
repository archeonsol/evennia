"""Namespaced capability definitions for the authorization runtime."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass

from django.conf import settings

_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}$")


class InvalidCapability(ValueError):
    """Raised when an unknown or malformed capability is referenced."""


def normalize_capability(value: str) -> str:
    """Normalize and validate a namespaced capability identifier.

    Args:
        value: Capability identifier to normalize.

    Returns:
        The normalized identifier.

    Raises:
        InvalidCapability: The identifier is malformed.
    """

    key = str(value or "").strip().lower()
    if len(key) > 128 or not _CAPABILITY_RE.fullmatch(key):
        raise InvalidCapability(f"capability {value!r} must be a lowercase three-part namespace")
    return key


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """One registered unit of authority."""

    key: str
    description: str = ""
    delegable: bool = True
    sensitive: bool = False

    def __post_init__(self):
        """Validate and normalize the definition."""

        object.__setattr__(self, "key", normalize_capability(self.key))
        if len(self.description) > 512:
            raise InvalidCapability("capability description exceeds 512 characters")


class CapabilityRegistry:
    """Registry of capabilities and convenience bundles.

    Bundles expand to explicit capabilities. They do not imply a rank or confer
    authority by themselves.
    """

    def __init__(self):
        """Initialize an empty registry."""

        self._definitions: dict[str, CapabilityDefinition] = {}
        self._bundles: dict[str, frozenset[str]] = {}
        self._loaded_modules = False

    def register(self, definition: CapabilityDefinition) -> CapabilityDefinition:
        """Register a capability definition idempotently.

        Args:
            definition: Definition to register.

        Returns:
            The canonical registered definition.
        """

        existing = self._definitions.get(definition.key)
        if existing is not None and existing != definition:
            raise InvalidCapability(f"conflicting definition for {definition.key}")
        self._definitions[definition.key] = definition
        return definition

    def register_bundle(self, key: str, capabilities) -> frozenset[str]:
        """Register a named capability bundle.

        Args:
            key: Administrative bundle name.
            capabilities: Capability identifiers in the bundle.

        Returns:
            The normalized immutable capability set.
        """

        bundle_key = str(key or "").strip().lower()
        if not bundle_key or len(bundle_key) > 64:
            raise InvalidCapability("bundle key must contain 1-64 characters")
        values = frozenset(self.require(cap).key for cap in capabilities)
        self._bundles[bundle_key] = values
        return values

    def require(self, key: str) -> CapabilityDefinition:
        """Return a definition or fail closed for an unknown reference."""

        normalized = normalize_capability(key)
        # The finite R3E compiler must preserve arbitrary historical permission
        # tags (many games used command names as permissions). Quarantine this
        # compatibility wildcard to the legacy namespace; R3F deletes it with
        # the permission hierarchy and requires explicit definitions everywhere.
        if normalized.startswith("legacy.permission.") and normalized not in self._definitions:
            self.register(
                CapabilityDefinition(
                    normalized,
                    description="Generated compatibility capability for R3 migration",
                    delegable=False,
                )
            )
        try:
            return self._definitions[normalized]
        except KeyError as err:
            raise InvalidCapability(f"unknown capability {normalized!r}") from err

    def expand_bundle(self, key: str) -> frozenset[str]:
        """Return the explicit capabilities in a bundle."""

        try:
            return self._bundles[str(key).strip().lower()]
        except KeyError as err:
            raise InvalidCapability(f"unknown capability bundle {key!r}") from err

    def definitions(self) -> tuple[CapabilityDefinition, ...]:
        """Return definitions in deterministic key order."""

        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def load_modules(self) -> None:
        """Import configured registration modules and invoke their hook.

        Each module may expose ``register_capabilities(registry)``. Import and
        registration errors deliberately abort startup.
        """

        if self._loaded_modules:
            return
        for path in getattr(settings, "AUTHORIZATION_CAPABILITY_MODULES", ()):
            module = importlib.import_module(path)
            register = getattr(module, "register_capabilities", None)
            if register is None:
                raise InvalidCapability(
                    f"authorization module {path!r} has no register_capabilities()"
                )
            register(self)
        self._loaded_modules = True


capability_registry = CapabilityRegistry()


def _register_engine_defaults() -> None:
    """Register the engine's game-agnostic authority vocabulary."""

    keys = {
        "engine.authorization.break_glass": (False, True),
        "engine.object.view": (True, False),
        "engine.object.edit": (True, False),
        "engine.object.delete": (False, True),
        "engine.object.control": (True, True),
        "engine.object.move": (True, False),
        "engine.object.examine": (True, False),
        "engine.object.read": (True, False),
        "engine.object.write": (True, False),
        "engine.object.create": (True, False),
        "engine.object.get": (True, False),
        "engine.object.drop": (True, False),
        "engine.object.call": (True, False),
        "engine.object.teleport": (False, True),
        "engine.object.teleport_here": (False, True),
        "engine.object.msg": (True, False),
        "engine.object.tell": (False, True),
        "engine.object.boot": (False, True),
        "engine.exit.traverse": (True, False),
        "engine.character.puppet": (False, True),
        "engine.command.execute": (True, False),
        "engine.channel.listen": (True, False),
        "engine.channel.send": (True, False),
        "engine.channel.control": (False, True),
        "engine.help.read": (True, False),
        "engine.script.control": (False, True),
        "engine.system.inspect": (False, True),
    }
    for key, (delegable, sensitive) in keys.items():
        capability_registry.register(
            CapabilityDefinition(key, delegable=delegable, sensitive=sensitive)
        )
    for legacy in ("guest", "player", "helper", "builder", "admin", "developer"):
        capability_registry.register(
            CapabilityDefinition(f"legacy.permission.{legacy}", delegable=False)
        )

    capability_registry.register_bundle(
        "world_builder",
        (
            "engine.object.view",
            "engine.object.edit",
            "engine.object.move",
            "engine.object.examine",
        ),
    )
    capability_registry.register_bundle(
        "moderator",
        ("engine.channel.listen", "engine.channel.send", "engine.channel.control"),
    )


_register_engine_defaults()
