"""Public authorization service and per-resource rollout facade."""

from __future__ import annotations

import random
import time
import uuid

from django.conf import settings

from evennia.server.models import AuthorizationAuditEvent
from evennia.utils import logger

from .engine import AuthorizationContext, AuthorizationDecision, evaluate
from .storage import (
    load_grants,
    load_policy,
    load_resource,
    principal_is_suspended,
    resource_ref,
)


def resource_policy_mode(resource) -> str:
    """Resolve the rollout mode for a resource kind."""

    kind = resource_ref(resource).split(":", 1)[0]
    modes = getattr(settings, "AUTHORIZATION_RESOURCE_POLICIES", {})
    mode = str(modes.get(kind, "legacy")).lower()
    if mode not in {"legacy", "diagnostic", "live"}:
        raise ValueError(f"invalid authorization mode {mode!r} for {kind}")
    return mode


def authorize(
    principal,
    resource,
    access_type: str,
    *,
    context=None,
    default: bool = False,
) -> AuthorizationDecision:
    """Evaluate a persisted structured policy for one resource operation."""

    started = time.perf_counter()
    try:
        grants = load_grants(principal)
        resource_snapshot = load_resource(resource)
    except Exception:
        logger.log_trace("authorization state unavailable")
        return AuthorizationDecision(False, "authorization_unavailable")

    def finish(decision):
        """Record low-cardinality timing and return the decision."""

        try:
            from evennia.server.prometheus_metrics import record_authorization_decision

            record_authorization_decision(
                resource_snapshot.resource_kind,
                decision.allowed,
                time.perf_counter() - started,
            )
        except Exception:
            pass
        return decision

    break_glass = False
    for scope in grants.by_capability.get("engine.authorization.break_glass", ()):
        kind, key = (scope.kind, scope.key) if hasattr(scope, "kind") else scope[-2:]
        constraints = dict(getattr(scope, "constraints", ()))
        required_session = constraints.get("session_id")
        session_matches = required_session is None or str(
            getattr(getattr(context, "session", None), "sessid", "")
        ) == str(required_session)
        if session_matches and (kind, key) in {
            ("world", "*"),
            ("resource", resource_snapshot.resource_ref),
        }:
            break_glass = True
            break
    try:
        suspended = principal_is_suspended(principal)
    except Exception:
        logger.log_trace("authorization principal state unavailable")
        return finish(AuthorizationDecision(False, "authorization_unavailable"))
    if break_glass and not suspended:
        try:
            AuthorizationAuditEvent.objects.create(
                event_id=uuid.uuid4().hex,
                kind="break_glass_used",
                principal_ref=grants.principal_ref,
                capability="engine.authorization.break_glass",
                resource_ref=resource_snapshot.resource_ref,
                reason=str(access_type)[:64],
            )
        except Exception:
            logger.log_trace("break-glass audit failed; authorization denied")
            return finish(AuthorizationDecision(False, "break_glass_audit_failed"))
        return finish(
            AuthorizationDecision(
                allowed=True,
                reason_code="break_glass",
                capability="engine.authorization.break_glass",
                matched_scopes=("world:*",),
                public_reason="",
            )
        )
    try:
        policy = load_policy(resource, access_type)
    except Exception:
        logger.log_trace("authorization policy is invalid or unavailable")
        return finish(AuthorizationDecision(False, "policy_invalid"))
    if policy is None:
        return finish(
            AuthorizationDecision(
                allowed=bool(default),
                reason_code="no_structured_policy",
                public_reason="" if default else "Access denied.",
            )
        )
    auth_context = context or AuthorizationContext(principal=principal, resource=resource)
    auth_context.suspended = suspended
    return finish(evaluate(policy, grants, resource_snapshot, auth_context))


def access_check(
    resource,
    principal,
    access_type: str,
    *,
    default: bool,
    legacy_evaluator,
    context=None,
) -> tuple[bool, AuthorizationDecision | None]:
    """Apply per-kind rollout while retaining the legacy oracle through R3E."""

    mode = resource_policy_mode(resource)
    if mode == "legacy":
        return bool(legacy_evaluator()), None

    # Transitional resources commonly have no structured override yet. Check
    # the negative-cached policy package first so legacy fallback does not load
    # grants, suspension state, or scope labels merely to discover there is no
    # policy to evaluate.
    try:
        policy = load_policy(resource, access_type)
    except Exception:
        logger.log_trace("authorization policy is invalid or unavailable")
        # Re-enter the full evaluator so an audited break-glass grant can still
        # recover a malformed policy. Ordinary principals fail closed.
        decision = authorize(principal, resource, access_type, context=context, default=default)
        if mode == "live":
            return decision.allowed, decision
        return bool(legacy_evaluator()), decision
    if policy is None:
        decision = AuthorizationDecision(
            allowed=bool(default),
            reason_code="no_structured_policy",
            public_reason="" if default else "Access denied.",
        )
        return bool(legacy_evaluator()), decision

    decision = authorize(principal, resource, access_type, context=context, default=default)
    if mode == "live":
        return decision.allowed, decision

    # Diagnostic is intentionally sampled; offline differential remains the
    # promotion gate and avoids doubling every production authorization check.
    legacy = bool(legacy_evaluator())
    sample = float(getattr(settings, "AUTHORIZATION_DIAGNOSTIC_SAMPLE_RATE", 0.0) or 0.0)
    if sample > 0 and random.random() < sample and legacy != decision.allowed:
        AuthorizationAuditEvent.objects.create(
            event_id=uuid.uuid4().hex,
            kind="decision_divergence",
            principal_ref=load_grants(principal).principal_ref,
            resource_ref=resource_ref(resource),
            reason=f"{access_type}:legacy={legacy}:new={decision.allowed}",
            data={"decision_reason": decision.reason_code},
        )
    return legacy, decision


def authorized_affordances(principal, resource, access_types) -> tuple[str, ...]:
    """Return public operations allowed by structured policies.

    This is the RenderNode/webclient seam. It never exposes failed requirements
    or privileged staff reasoning to the client.
    """

    allowed = []
    for access_type in access_types:
        decision = authorize(principal, resource, access_type, default=False)
        if decision.allowed:
            allowed.append(str(access_type))
    return tuple(allowed)
