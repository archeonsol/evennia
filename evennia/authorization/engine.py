"""Pure authorization evaluation and explainable decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from .policy import (AllOf, Always, AnyOf, Never, Not, Policy,
                     PredicateRequirement, RequiresCapability,
                     get_predicate_provider, register_predicate_provider)


@dataclass(frozen=True, slots=True)
class GrantScope:
    """One positive scope match with bounded runtime constraints."""

    origin: str
    kind: str
    key: str
    constraints: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class GrantSnapshot:
    """Slow-changing positive grants for one principal."""

    principal_ref: str
    by_capability: Mapping[str, frozenset[tuple[str, ...]]]
    generation: int
    valid_until: float | None = None


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Resource-indexed scope labels and policy generation."""

    resource_ref: str
    resource_kind: str
    labels: frozenset[str]
    generation: int


@dataclass(slots=True)
class AuthorizationContext:
    """Ephemeral state available to contextual policy providers."""

    principal: object
    resource: object
    action: object = None
    session: object = None
    suspended: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    memo: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Explainable result of one authorization request."""

    allowed: bool
    reason_code: str
    capability: str = ""
    failed_requirements: tuple[str, ...] = ()
    matched_scopes: tuple[str, ...] = ()
    public_reason: str = "Access denied."
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)


def _matching_scopes(
    grants,
    resource: ResourceSnapshot,
    context: AuthorizationContext,
    principal_scope: str = "effective",
) -> tuple[str, ...]:
    """Return applicable positive scope grants without graph traversal."""

    matched = []
    for grant in grants:
        constraints = ()
        if isinstance(grant, GrantScope):
            origin, kind, key, constraints = (
                grant.origin,
                grant.kind,
                grant.key,
                grant.constraints,
            )
        elif len(grant) == 3:
            origin, kind, key = grant
        else:
            origin, (kind, key) = "", grant
        if (
            principal_scope != "effective"
            and origin
            and not origin.startswith(f"{principal_scope}:")
        ):
            continue
        constraints = dict(constraints)
        required_session = constraints.get("session_id")
        if required_session is not None and str(
            getattr(context.session, "sessid", "")
        ) != str(required_session):
            continue
        if kind == "world" and key == "*":
            matched.append("world:*")
        elif kind == "resource" and key == resource.resource_ref:
            matched.append(f"resource:{key}")
        elif kind == "label" and key in resource.labels:
            matched.append(f"label:{key}")
    return tuple(sorted(matched))


def _evaluate_node(
    policy: Policy,
    grants: GrantSnapshot,
    resource: ResourceSnapshot,
    context: AuthorizationContext,
):
    """Evaluate one policy node and return result details."""

    if isinstance(policy, Always):
        return True, (), (), ""
    if isinstance(policy, Never):
        return False, ("never",), (), ""
    if isinstance(policy, RequiresCapability):
        scopes = _matching_scopes(
            grants.by_capability.get(policy.capability, ()),
            resource,
            context,
            policy.principal_scope,
        )
        if scopes:
            return True, (), scopes, policy.capability
        return False, (f"missing:{policy.capability}",), (), policy.capability
    if isinstance(policy, PredicateRequirement):
        memo_key = (policy.key, tuple(sorted(policy.params.items())))
        if memo_key not in context.memo:
            provider = get_predicate_provider(policy.key)
            context.memo[memo_key] = bool(provider(context, policy.params))
        passed = context.memo[memo_key]
        return passed, (() if passed else (f"predicate:{policy.key}",)), (), ""
    if isinstance(policy, AllOf):
        failures = []
        scopes = []
        capability = ""
        for part in policy.parts:
            passed, part_failures, part_scopes, part_capability = _evaluate_node(
                part, grants, resource, context
            )
            scopes.extend(part_scopes)
            capability = capability or part_capability
            if not passed:
                failures.extend(part_failures)
        return not failures, tuple(failures), tuple(scopes), capability
    if isinstance(policy, AnyOf):
        failures = []
        for part in policy.parts:
            passed, part_failures, part_scopes, capability = _evaluate_node(
                part, grants, resource, context
            )
            if passed:
                return True, (), part_scopes, capability
            failures.extend(part_failures)
        return False, tuple(failures), (), ""
    if isinstance(policy, Not):
        passed, _, _, _ = _evaluate_node(policy.inner, grants, resource, context)
        return (not passed), (() if not passed else ("negated_requirement",)), (), ""
    raise TypeError(f"unsupported policy node {type(policy).__name__}")


def evaluate(
    policy: Policy,
    grants: GrantSnapshot,
    resource: ResourceSnapshot,
    context: AuthorizationContext,
) -> AuthorizationDecision:
    """Join principal grants, resource scopes, and ephemeral context."""

    if context.suspended:
        return AuthorizationDecision(False, "principal_suspended")
    allowed, failures, scopes, capability = _evaluate_node(
        policy, grants, resource, context
    )
    return AuthorizationDecision(
        allowed=allowed,
        reason_code="requirements_satisfied" if allowed else "requirements_unmet",
        capability=capability,
        failed_requirements=failures,
        matched_scopes=scopes,
        public_reason="" if allowed else "Access denied.",
    )


def _not_suspended(context, params):
    """Default universal principal-state predicate."""

    return not context.suspended


register_predicate_provider("not_suspended", _not_suspended)


def _setting_enabled(context, params):
    """Return the truth value of one explicitly named Django setting."""

    from django.conf import settings

    key = str(params.get("key", ""))
    return bool(key and key.isupper() and getattr(settings, key, False))


register_predicate_provider("setting.enabled", _setting_enabled)


def _principal_controls_resource(context, params):
    """Return whether the principal is the resource's active controller."""

    principal = context.principal
    resource = context.resource
    if principal is resource:
        return True
    principal_account = (
        getattr(principal, "puppeteer", None)
        or getattr(principal, "account", None)
        or (principal if "account" in principal.__class__.__module__.lower() else None)
    )
    resource_account = getattr(resource, "puppeteer", None) or getattr(
        resource, "account", None
    )
    return principal_account is not None and principal_account is resource_account


register_predicate_provider("principal.controls_resource", _principal_controls_resource)


def _principal_holds_resource(context, params):
    """Return whether the protected resource is directly held by the principal."""

    principal = getattr(context.principal, "effective", None) or context.principal
    return context.resource in getattr(principal, "contents", ())


register_predicate_provider("principal.holds_resource", _principal_holds_resource)


def _principal_is_message_participant(context, params):
    """Return whether the principal sends or receives the protected message."""

    principal = context.principal
    resource = context.resource
    candidates = {
        principal,
        getattr(principal, "account", None),
        getattr(principal, "puppeteer", None),
    }
    participants = set(getattr(resource, "senders", ())) | set(
        getattr(resource, "receivers", ())
    )
    return bool(candidates & participants)


register_predicate_provider(
    "principal.message_participant", _principal_is_message_participant
)
