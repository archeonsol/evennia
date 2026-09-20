"""Public authorization service and per-resource rollout facade."""

from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager

from evennia.server.models import AuthorizationAuditEvent
from evennia.utils import logger

from .engine import AuthorizationContext, AuthorizationDecision, evaluate
from .policy import RequiresCapability, validate_policy
from .storage import load_grants, load_policy, load_resource, principal_is_suspended, resource_ref

_DECISION_SCOPE: contextvars.ContextVar = contextvars.ContextVar(
    "authorization_decision_scope", default=None
)
_SCOPE_CLOSED = "__closed__"


def _active_scope():
    """Return the live decision scope, or None when there is none.

    A scope dict is closed when its context manager exits. Closing matters
    because asyncio tasks copy the context by reference: a task spawned inside
    a render would otherwise keep using a memo that outlived the render.
    """

    scope = _DECISION_SCOPE.get()
    if scope is None or scope.get(_SCOPE_CLOSED):
        return None
    return scope


@contextmanager
def decision_scope():
    """Memoize authorization decisions for the duration of one render.

    A look evaluates visibility per object and again for the search lock, and
    each answer costs a full policy evaluation. Within one render the inputs
    cannot change — render paths do not mutate grants, policies or suspensions —
    so decisions are cached by identity for the scope's lifetime and dropped
    with it. Nested scopes reuse the outer cache, so wrapping both the render
    entry point and ``filter_visible`` shares one memo. Cache entries keep the
    principal and resource alive, so a recycled ``id()`` can never alias.

    Yields:
        dict: The decision cache for this scope.

    """

    existing = _active_scope()
    if existing is not None:
        yield existing
        return
    scope = {}
    token = _DECISION_SCOPE.set(scope)
    try:
        yield scope
    finally:
        scope[_SCOPE_CLOSED] = True
        _DECISION_SCOPE.reset(token)


def authorize(
    principal,
    resource,
    access_type: str,
    *,
    context=None,
    default: bool = False,
) -> AuthorizationDecision:
    """Evaluate a persisted structured policy for one resource operation."""

    scope = _active_scope()
    cache_key = None
    if scope is not None:
        cache_key = (
            id(principal),
            id(resource),
            str(access_type),
            getattr(getattr(context, "session", None), "sessid", None),
        )
        cached = scope.get(cache_key)
        if cached is not None:
            try:
                from evennia.server.prometheus_metrics import (
                    record_authorization_decision_cache_hit,
                )

                record_authorization_decision_cache_hit()
            except Exception:
                pass
            return cached[2]

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
                decision.reason_code,
            )
        except Exception:
            pass
        if cache_key is not None:
            scope[cache_key] = (principal, resource, decision)
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
                allowed=False,
                reason_code="no_structured_policy",
                public_reason="Access denied.",
            )
        )
    try:
        validate_policy(policy)
    except (TypeError, ValueError):
        logger.log_trace("authorization policy contains an unresolved reference")
        return finish(AuthorizationDecision(False, "policy_invalid"))
    auth_context = context or AuthorizationContext(principal=principal, resource=resource)
    auth_context.suspended = suspended
    return finish(evaluate(policy, grants, resource_snapshot, auth_context))


def access_check(
    resource,
    principal,
    access_type: str,
    *,
    default: bool,
    context=None,
) -> tuple[bool, AuthorizationDecision]:
    """Evaluate the capability authority behind stable ``access()`` facades."""

    decision = authorize(principal, resource, access_type, context=context, default=default)
    return decision.allowed, decision


def has_capability(principal, capability: str, *, resource=None, session=None) -> bool:
    """Check one explicit capability without a synthetic resource policy."""

    resource = resource or principal
    scope = _active_scope()
    cache_key = None
    if scope is not None:
        cache_key = (
            id(principal),
            id(resource),
            f"capability:{capability}",
            getattr(session, "sessid", None),
        )
        cached = scope.get(cache_key)
        if cached is not None:
            try:
                from evennia.server.prometheus_metrics import (
                    record_authorization_decision_cache_hit,
                )

                record_authorization_decision_cache_hit()
            except Exception:
                pass
            return cached[2]
    try:
        grants = load_grants(principal)
        snapshot = load_resource(resource)
        suspended = principal_is_suspended(principal)
    except Exception:
        logger.log_trace("authorization capability check unavailable")
        return False
    context = AuthorizationContext(
        principal=principal,
        resource=resource,
        session=session,
        suspended=suspended,
    )
    break_glass = evaluate(
        RequiresCapability("engine.authorization.break_glass"),
        grants,
        snapshot,
        context,
    ).allowed
    if break_glass:
        try:
            AuthorizationAuditEvent.objects.create(
                event_id=uuid.uuid4().hex,
                kind="break_glass_used",
                principal_ref=grants.principal_ref,
                capability="engine.authorization.break_glass",
                resource_ref=snapshot.resource_ref,
                reason=f"capability:{capability}"[:64],
            )
        except Exception:
            logger.log_trace("break-glass audit failed; capability denied")
            return False
        if cache_key is not None:
            scope[cache_key] = (principal, resource, True)
        return True
    result = evaluate(RequiresCapability(capability), grants, snapshot, context).allowed
    if cache_key is not None:
        scope[cache_key] = (principal, resource, result)
    return result


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
