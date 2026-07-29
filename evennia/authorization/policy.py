"""Immutable structured authorization policies and predicate providers."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from django.conf import settings

from .capabilities import capability_registry, normalize_capability

POLICY_SCHEMA = "auth.policy.v1"
_PREDICATE_PROVIDERS: dict[str, object] = {}


def _primitive_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Validate a shallow primitive parameter mapping."""

    if len(dict(value or {})) > 32:
        raise ValueError("policy parameter mapping exceeds 32 entries")
    result = {}
    for key, item in dict(value or {}).items():
        if not isinstance(key, str) or len(key) > 64:
            raise ValueError("policy parameter keys must be bounded strings")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError("policy parameters must be JSON scalar values")
        result[key] = item
    return MappingProxyType(result)


class Policy:
    """Base class for immutable policy nodes."""

    __slots__ = ()

    def to_data(self) -> dict:
        """Serialize this node to bounded primitive data."""

        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PolicyTemplate:
    """Named, versioned operation policies for a resource family."""

    key: str
    operations: Mapping[str, Policy]
    version: int = 1

    def __post_init__(self):
        """Validate and freeze operation policies."""

        key = str(self.key or "").strip().lower()
        if not key or len(key) > 128:
            raise ValueError("policy template key must contain 1-128 characters")
        operations = {}
        for operation, policy in dict(self.operations).items():
            normalized = str(operation).strip().lower()
            if not normalized or not isinstance(policy, Policy):
                raise ValueError("template operations require a key and Policy")
            operations[normalized] = policy
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "operations", MappingProxyType(operations))


class PolicyRegistry:
    """Resolve inherited templates without storing copies on every resource."""

    def __init__(self):
        """Initialize empty template and binding registries."""

        self._templates: dict[str, PolicyTemplate] = {}
        self._kind_bindings: dict[str, str] = {}
        self._type_bindings: dict[str, str] = {}

    def register(self, template: PolicyTemplate) -> PolicyTemplate:
        """Register a template idempotently."""

        for policy in template.operations.values():
            validate_policy(policy)
        existing = self._templates.get(template.key)
        if existing is not None and existing != template:
            raise ValueError(f"conflicting authorization template {template.key!r}")
        self._templates[template.key] = template
        return template

    def bind_kind(self, resource_kind: str, template_key: str) -> None:
        """Bind a resource kind to a registered default template."""

        self.require(template_key)
        self._kind_bindings[str(resource_kind).lower()] = template_key.lower()

    def bind_type(self, dotted_type: str, template_key: str) -> None:
        """Bind an exact Python resource type to a registered template."""

        self.require(template_key)
        self._type_bindings[str(dotted_type).lower()] = template_key.lower()

    def require(self, key: str) -> PolicyTemplate:
        """Return a template or fail closed."""

        try:
            return self._templates[str(key).lower()]
        except KeyError as err:
            raise ValueError(f"unknown authorization template {key!r}") from err

    def resolve(self, resource, resource_kind: str, operation: str) -> Policy | None:
        """Resolve an authored policy, inherited type binding, then kind default."""

        operation = str(operation).lower()
        instance_policies = vars(resource).get("authorization_policies")
        if instance_policies is not None:
            policy = dict(instance_policies).get(operation)
            if policy is not None:
                if not isinstance(policy, Policy):
                    raise TypeError("instance authorization policies must be Policy nodes")
                return policy
        explicit_policies = getattr(type(resource), "authorization_policies", None)
        if explicit_policies is not None:
            policy = dict(explicit_policies).get(operation)
            if policy is not None:
                if not isinstance(policy, Policy):
                    raise TypeError("authorization_policies values must be Policy nodes")
                return policy
        if resource_kind == "command" and operation == "cmd":
            explicit = getattr(type(resource), "authorization_policy", None)
            if explicit is not None:
                if not isinstance(explicit, Policy):
                    raise TypeError("command authorization_policy must be a Policy node")
                return explicit

        explicit = getattr(type(resource), "authorization_policy_template", "")
        cls = resource.__class__
        template_key = str(explicit).lower() if explicit else ""
        if not template_key:
            for base in cls.__mro__:
                dotted = f"{base.__module__}.{base.__qualname__}".lower()
                template_key = self._type_bindings.get(dotted, "")
                if template_key:
                    break
        template_key = template_key or self._kind_bindings.get(resource_kind.lower())
        if not template_key:
            return None
        return self.require(template_key).operations.get(operation)


policy_registry = PolicyRegistry()


def validate_policy(policy: Policy) -> None:
    """Fail closed on unresolved capability or predicate references."""

    if isinstance(policy, RequiresCapability):
        capability_registry.require(policy.capability)
    elif isinstance(policy, PredicateRequirement):
        get_predicate_provider(policy.key)
    elif isinstance(policy, (AllOf, AnyOf)):
        for part in policy.parts:
            validate_policy(part)
    elif isinstance(policy, Not):
        validate_policy(policy.inner)


def load_policy_modules() -> None:
    """Load game/plugin policy registration modules exactly once."""

    if getattr(load_policy_modules, "_loaded", False):
        return
    for path in getattr(settings, "AUTHORIZATION_POLICY_MODULES", ()):
        module = importlib.import_module(path)
        register = getattr(module, "register_authorization_policies", None)
        if register is None:
            raise ValueError(
                f"authorization module {path!r} has no register_authorization_policies()"
            )
        register(policy_registry)
    load_policy_modules._loaded = True


@dataclass(frozen=True, slots=True)
class Always(Policy):
    """An explicit public-access requirement."""

    def to_data(self) -> dict:
        """Serialize the node."""

        return {"schema": POLICY_SCHEMA, "type": "always"}


@dataclass(frozen=True, slots=True)
class Never(Policy):
    """An explicit inaccessible requirement."""

    def to_data(self) -> dict:
        """Serialize the node."""

        return {"schema": POLICY_SCHEMA, "type": "never"}


@dataclass(frozen=True, slots=True)
class RequiresCapability(Policy):
    """Require a named capability with any matching positive grant."""

    capability: str
    principal_scope: str = "effective"

    def __post_init__(self):
        """Normalize the capability identifier."""

        object.__setattr__(self, "capability", normalize_capability(self.capability))
        scope = str(self.principal_scope).lower()
        if scope not in {"effective", "account", "object", "session"}:
            raise ValueError(f"invalid capability principal scope {scope!r}")
        object.__setattr__(self, "principal_scope", scope)

    def to_data(self) -> dict:
        """Serialize the node."""

        return {
            "schema": POLICY_SCHEMA,
            "type": "capability",
            "capability": self.capability,
            "principal_scope": self.principal_scope,
        }


@dataclass(frozen=True, slots=True)
class PredicateRequirement(Policy):
    """Invoke a registered contextual provider with primitive parameters."""

    key: str
    params: Mapping[str, Any] = None

    def __post_init__(self):
        """Validate the provider key and freeze parameters."""

        key = str(self.key or "").strip().lower()
        if not key or len(key) > 128:
            raise ValueError("predicate provider key must contain 1-128 characters")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "params", _primitive_mapping(self.params))

    def to_data(self) -> dict:
        """Serialize the node."""

        return {
            "schema": POLICY_SCHEMA,
            "type": "predicate",
            "key": self.key,
            "params": dict(self.params),
        }


@dataclass(frozen=True, slots=True)
class AllOf(Policy):
    """Require every child policy."""

    parts: tuple[Policy, ...]

    def __post_init__(self):
        """Require at least one typed child."""

        object.__setattr__(self, "parts", tuple(self.parts))
        if not self.parts or not all(isinstance(part, Policy) for part in self.parts):
            raise ValueError("all policy requires at least one Policy child")

    def to_data(self) -> dict:
        """Serialize the node."""

        return {
            "schema": POLICY_SCHEMA,
            "type": "all",
            "parts": [part.to_data() for part in self.parts],
        }


@dataclass(frozen=True, slots=True)
class AnyOf(Policy):
    """Require at least one child policy."""

    parts: tuple[Policy, ...]

    def __post_init__(self):
        """Require at least one typed child."""

        object.__setattr__(self, "parts", tuple(self.parts))
        if not self.parts or not all(isinstance(part, Policy) for part in self.parts):
            raise ValueError("any policy requires at least one Policy child")

    def to_data(self) -> dict:
        """Serialize the node."""

        return {
            "schema": POLICY_SCHEMA,
            "type": "any",
            "parts": [part.to_data() for part in self.parts],
        }


@dataclass(frozen=True, slots=True)
class Not(Policy):
    """Invert one contextual requirement.

    This is policy structure, not a negative grant. It is appropriate for state
    requirements such as ``not suspended``.
    """

    inner: Policy

    def to_data(self) -> dict:
        """Serialize the node."""

        return {"schema": POLICY_SCHEMA, "type": "not", "inner": self.inner.to_data()}


def policy_from_data(data: Mapping[str, Any], _depth: int = 0) -> Policy:
    """Compile validated primitive data into an immutable policy tree."""

    if _depth > 16:
        raise ValueError("authorization policy exceeds maximum depth")
    if not isinstance(data, Mapping) or data.get("schema") != POLICY_SCHEMA:
        raise ValueError("authorization policy must use auth.policy.v1")
    kind = data.get("type")
    if kind == "always":
        return Always()
    if kind == "never":
        return Never()
    if kind == "capability":
        policy = RequiresCapability(
            data.get("capability", ""), data.get("principal_scope", "effective")
        )
        capability_registry.require(policy.capability)
        return policy
    if kind == "predicate":
        policy = PredicateRequirement(data.get("key", ""), data.get("params"))
        get_predicate_provider(policy.key)
        return policy
    if kind == "all":
        parts = data.get("parts") or ()
        if len(parts) > 64:
            raise ValueError("authorization policy node exceeds 64 children")
        return AllOf(tuple(policy_from_data(part, _depth + 1) for part in parts))
    if kind == "any":
        parts = data.get("parts") or ()
        if len(parts) > 64:
            raise ValueError("authorization policy node exceeds 64 children")
        return AnyOf(tuple(policy_from_data(part, _depth + 1) for part in parts))
    if kind == "not":
        return Not(policy_from_data(data.get("inner") or {}, _depth + 1))
    raise ValueError(f"unknown authorization policy node {kind!r}")


def register_predicate_provider(key: str, provider) -> None:
    """Register a synchronous contextual predicate provider."""

    normalized = str(key or "").strip().lower()
    if not normalized or not callable(provider):
        raise ValueError("predicate providers require a key and callable")
    existing = _PREDICATE_PROVIDERS.get(normalized)
    if existing is not None and existing is not provider:
        raise ValueError(f"predicate provider {normalized!r} is already registered")
    _PREDICATE_PROVIDERS[normalized] = provider


def get_predicate_provider(key: str):
    """Return a registered provider or fail closed."""

    try:
        return _PREDICATE_PROVIDERS[key]
    except KeyError as err:
        raise ValueError(f"unknown authorization predicate {key!r}") from err
