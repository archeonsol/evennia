"""Namespaced capability definitions for the authorization runtime."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass

from django.conf import settings

_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}$")
_LIFECYCLE_STATUSES = frozenset({"active", "internal", "legacy", "retired"})


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
        raise InvalidCapability(
            f"capability {value!r} must be a lowercase three-part namespace"
        )
    return key


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """One registered unit of authority."""

    key: str
    description: str = ""
    delegable: bool = True
    sensitive: bool = False
    category: str = "Uncategorized"
    status: str = "active"

    def __post_init__(self):
        """Validate and normalize the definition."""

        object.__setattr__(self, "key", normalize_capability(self.key))
        if len(self.description) > 512:
            raise InvalidCapability("capability description exceeds 512 characters")
        category = str(self.category or "Uncategorized").strip()
        if len(category) > 80:
            raise InvalidCapability("capability category exceeds 80 characters")
        status = str(self.status or "active").strip().lower()
        if status not in _LIFECYCLE_STATUSES:
            raise InvalidCapability(f"unknown capability lifecycle status {status!r}")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "status", status)


@dataclass(frozen=True, slots=True)
class BundleDefinition:
    """One described administrative package of explicit capabilities."""

    key: str
    capabilities: frozenset[str]
    description: str = ""
    category: str = "Uncategorized"
    status: str = "active"

    def __post_init__(self):
        """Normalize display metadata without changing bundle expansion."""

        key = str(self.key or "").strip().lower()
        if not key or len(key) > 64:
            raise InvalidCapability("bundle key must contain 1-64 characters")
        if len(self.description) > 512:
            raise InvalidCapability("bundle description exceeds 512 characters")
        category = str(self.category or "Uncategorized").strip()
        if len(category) > 80:
            raise InvalidCapability("bundle category exceeds 80 characters")
        status = str(self.status or "active").strip().lower()
        if status not in _LIFECYCLE_STATUSES:
            raise InvalidCapability(f"unknown bundle lifecycle status {status!r}")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "status", status)


class CapabilityRegistry:
    """Registry of capabilities and convenience bundles.

    Bundles expand to explicit capabilities. They do not imply a rank or confer
    authority by themselves.
    """

    def __init__(self):
        """Initialize an empty registry."""

        self._definitions: dict[str, CapabilityDefinition] = {}
        self._bundles: dict[str, frozenset[str]] = {}
        self._bundle_definitions: dict[str, BundleDefinition] = {}
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

    def register_bundle(
        self,
        key: str,
        capabilities,
        *,
        description: str = "",
        category: str = "Uncategorized",
        status: str = "active",
    ) -> frozenset[str]:
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
        definition = BundleDefinition(
            key=bundle_key,
            capabilities=values,
            description=description,
            category=category,
            status=status,
        )
        self._bundles[bundle_key] = values
        self._bundle_definitions[bundle_key] = definition
        return values

    def require(self, key: str) -> CapabilityDefinition:
        """Return a definition or fail closed for an unknown reference."""

        normalized = normalize_capability(key)
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

    def bundle_definitions(self) -> tuple[BundleDefinition, ...]:
        """Return described bundles in deterministic key order."""

        return tuple(
            self._bundle_definitions[key] for key in sorted(self._bundle_definitions)
        )

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
        "engine.world.build": (True, True),
        "engine.help.manage": (True, False),
        "engine.moderation.manage": (False, True),
        "engine.runtime.manage": (False, True),
        # The engine console. Holding this is equivalent to shell access on the
        # game server: it carries a REPL, a SQL console, and process control.
        # Never delegable, always sensitive. See decision D1 in
        # `.agents/prompts/W1-console-implementation-plan.md`.
        "engine.console.access": (False, True),
        # Admits a non-superuser moderator to the console's moderation panel and
        # nothing else. The one internal boundary the console has.
        "engine.console.moderation": (False, True),
        # Narrow enforcement controls intentionally sit outside every bundle.
        "engine.console.moderation.address": (False, True),
        "engine.console.moderation.permanent": (False, True),
        "engine.channel.banned": (False, False),
        "engine.message.banned": (False, False),
        "engine.channel.listen": (True, False),
        "engine.channel.send": (True, False),
        "engine.channel.control": (False, True),
        "engine.message.read": (True, False),
        "engine.message.edit": (True, False),
        "engine.message.delete": (False, True),
        "engine.help.read": (True, False),
        "engine.script.control": (False, True),
        "engine.system.inspect": (False, True),
    }
    metadata = {
        "engine.authorization.break_glass": (
            "Temporarily bypass capability checks for audited emergency recovery.",
            "Emergency recovery",
        ),
        "engine.object.view": (
            "View an object through privileged engine surfaces.",
            "Objects",
        ),
        "engine.object.edit": (
            "Edit an object's stored fields and attributes.",
            "Objects",
        ),
        "engine.object.delete": ("Permanently delete an object.", "Objects"),
        "engine.object.control": (
            "Manage an object's authority and control policy.",
            "Objects",
        ),
        "engine.object.move": (
            "Move an object outside ordinary gameplay rules.",
            "Objects",
        ),
        "engine.object.examine": ("Inspect privileged object metadata.", "Objects"),
        "engine.object.read": ("Read protected object content.", "Objects"),
        "engine.object.write": ("Write protected object content.", "Objects"),
        "engine.object.create": (
            "Create objects through engine administration surfaces.",
            "Objects",
        ),
        "engine.object.get": (
            "Take an object through an explicit object policy.",
            "Objects",
        ),
        "engine.object.drop": (
            "Drop an object through an explicit object policy.",
            "Objects",
        ),
        "engine.object.call": (
            "Invoke an object through an explicit object policy.",
            "Objects",
        ),
        "engine.object.teleport": (
            "Teleport an object to another location.",
            "Objects",
        ),
        "engine.object.teleport_here": (
            "Teleport an object to the operator.",
            "Objects",
        ),
        "engine.object.msg": (
            "Send an administrative message through an object.",
            "Objects",
        ),
        "engine.object.tell": (
            "Send a privileged direct message to an object.",
            "Objects",
        ),
        "engine.object.boot": ("Disconnect sessions controlling an object.", "Objects"),
        "engine.exit.traverse": (
            "Traverse an exit through an explicit engine policy.",
            "Movement",
        ),
        "engine.character.puppet": (
            "Take control of a character through engine administration.",
            "Characters",
        ),
        "engine.command.execute": ("Execute a protected command.", "Commands"),
        "engine.world.build": (
            "Use the engine's unrestricted world-building tools.",
            "World building",
        ),
        "engine.help.manage": (
            "Create, inspect, and edit protected help entries.",
            "Help",
        ),
        "engine.moderation.manage": (
            "Use engine-level moderation operations.",
            "Moderation",
        ),
        "engine.runtime.manage": (
            "Control the running server and its registries.",
            "Runtime",
        ),
        "engine.console.access": (
            "Open the full engine console, including REPL, SQL, and process controls.",
            "Console",
        ),
        "engine.console.moderation": (
            "Open only the console's bounded moderation station.",
            "Console",
        ),
        "engine.console.moderation.address": (
            "Sanction a single raw address or device token from the moderation console.",
            "Console",
        ),
        "engine.console.moderation.permanent": (
            "Issue a moderation sanction that never expires.",
            "Console",
        ),
        "engine.channel.banned": (
            "Mark a principal as barred from channels.",
            "Restrictions",
        ),
        "engine.message.banned": (
            "Mark a principal as barred from direct messages.",
            "Restrictions",
        ),
        "engine.channel.listen": ("Listen to a protected channel.", "Channels"),
        "engine.channel.send": ("Send to a protected channel.", "Channels"),
        "engine.channel.control": ("Configure and moderate a channel.", "Channels"),
        "engine.message.read": ("Read protected stored messages.", "Messages"),
        "engine.message.edit": ("Edit protected stored messages.", "Messages"),
        "engine.message.delete": (
            "Permanently delete protected stored messages.",
            "Messages",
        ),
        "engine.help.read": ("Read a protected help entry.", "Help"),
        "engine.script.control": ("Start, stop, and manage engine scripts.", "Runtime"),
        "engine.system.inspect": (
            "Inspect protected server and process state.",
            "Runtime",
        ),
    }
    for key, (delegable, sensitive) in keys.items():
        description, category = metadata[key]
        capability_registry.register(
            CapabilityDefinition(
                key,
                description=description,
                delegable=delegable,
                sensitive=sensitive,
                category=category,
            )
        )
    capability_registry.register_bundle(
        "world_builder",
        (
            "engine.world.build",
            "engine.object.view",
            "engine.object.edit",
            "engine.object.move",
            "engine.object.examine",
        ),
        description="Core unrestricted world-building and object-editing authority.",
        category="Engine operations",
    )
    capability_registry.register_bundle(
        "moderator",
        (
            "engine.moderation.manage",
            "engine.channel.listen",
            "engine.channel.send",
            "engine.channel.control",
        ),
        description="Engine moderation and protected-channel control.",
        category="Engine operations",
    )
    capability_registry.register_bundle(
        "console_moderator",
        ("engine.console.moderation",),
        description="Access only the bounded console moderation station.",
        category="Engine operations",
    )
    capability_registry.register_bundle(
        "runtime_operator",
        (
            "engine.runtime.manage",
            "engine.moderation.manage",
            "engine.world.build",
            "engine.object.view",
            "engine.object.edit",
            "engine.object.delete",
            "engine.object.control",
            "engine.object.move",
            "engine.object.examine",
            "engine.object.teleport",
            "engine.object.teleport_here",
            "engine.object.tell",
            "engine.object.boot",
            "engine.channel.listen",
            "engine.channel.send",
            "engine.channel.control",
            "engine.script.control",
            "engine.system.inspect",
            "engine.console.access",
        ),
        description="Server-owner authority, including the full engine console and process control.",
        category="Engine operations",
    )


_register_engine_defaults()
