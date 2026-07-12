"""Capability-based authorization for engine and game resources."""

# Register the finite, auditable compatibility predicate set. Arbitrary lock
# functions are never imported into the new evaluator.
from . import legacy_providers as _legacy_providers  # noqa: F401, E402
from .action import RequiresAuthorization
from .capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    InvalidCapability,
    capability_registry,
    normalize_capability,
)
from .compiler import (
    CompilationError,
    CompilationResult,
    compile_lockstring,
    register_lock_compiler,
)
from .engine import (
    AuthorizationContext,
    AuthorizationDecision,
    GrantScope,
    GrantSnapshot,
    ResourceSnapshot,
    evaluate,
)
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
)
from .resources import ResourceAdapter, ResourceAdapterRegistry, resource_adapters

__all__ = [
    "AllOf",
    "Always",
    "AnyOf",
    "AuthorizationContext",
    "AuthorizationDecision",
    "CapabilityDefinition",
    "CapabilityRegistry",
    "CompilationError",
    "CompilationResult",
    "GrantSnapshot",
    "GrantScope",
    "InvalidCapability",
    "Never",
    "Not",
    "Policy",
    "PolicyRegistry",
    "PolicyTemplate",
    "PredicateRequirement",
    "RequiresCapability",
    "RequiresAuthorization",
    "ResourceSnapshot",
    "ResourceAdapter",
    "ResourceAdapterRegistry",
    "capability_registry",
    "compile_lockstring",
    "evaluate",
    "normalize_capability",
    "policy_from_data",
    "policy_registry",
    "register_predicate_provider",
    "register_lock_compiler",
    "resource_adapters",
]
