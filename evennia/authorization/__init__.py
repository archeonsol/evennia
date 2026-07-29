"""Capability-based authorization for engine and game resources."""

from .capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    InvalidCapability,
    capability_registry,
    normalize_capability,
)
from .engine import (
    AuthorizationContext,
    AuthorizationDecision,
    GrantScope,
    GrantSnapshot,
    ResourceSnapshot,
    evaluate,
)
from .handler import PolicyHandler
from .policy import (
    AllOf,
    Always,
    AnyOf,
    Never,
    Not,
    Policy,
    PolicyRegistry,
    PolicyTemplate,
    PredicateRequirement,
    RequiresCapability,
    policy_from_data,
    policy_registry,
    register_predicate_provider,
    validate_policy,
)
from .resources import ResourceAdapter, ResourceAdapterRegistry, resource_adapters
from .service import access_check, authorize, authorized_affordances, has_capability

__all__ = [
    "AllOf",
    "Always",
    "AnyOf",
    "AuthorizationContext",
    "AuthorizationDecision",
    "access_check",
    "authorize",
    "authorized_affordances",
    "CapabilityDefinition",
    "CapabilityRegistry",
    "GrantSnapshot",
    "GrantScope",
    "InvalidCapability",
    "Never",
    "Not",
    "Policy",
    "PolicyRegistry",
    "PolicyTemplate",
    "PolicyHandler",
    "PredicateRequirement",
    "RequiresCapability",
    "ResourceSnapshot",
    "ResourceAdapter",
    "ResourceAdapterRegistry",
    "capability_registry",
    "evaluate",
    "has_capability",
    "normalize_capability",
    "policy_from_data",
    "policy_registry",
    "register_predicate_provider",
    "validate_policy",
    "resource_adapters",
]
