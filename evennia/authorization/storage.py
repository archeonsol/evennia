"""Persistent grants, scopes, policies, and split authorization caches."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.utils import timezone

from evennia.server.models import (
    AuthorizationAuditEvent,
    AuthorizationGrant,
    AuthorizationPolicyOverride,
    AuthorizationPrincipalState,
    AuthorizationScopeLabel,
)
from evennia.utils import logger

from .capabilities import capability_registry
from .engine import GrantScope, GrantSnapshot, ResourceSnapshot
from .policy import policy_from_data, policy_registry
from .resources import resource_adapters

_MAX_CACHE = 20_000
_principal_generation: dict[str, int] = {}
_resource_generation: dict[str, int] = {}
_principal_cache: OrderedDict[str, GrantSnapshot] = OrderedDict()
_resource_cache: OrderedDict[str, ResourceSnapshot] = OrderedDict()
_policy_cache: OrderedDict[tuple[str, str], object] = OrderedDict()
_suspension_cache: OrderedDict[str, tuple[int, bool, float | None]] = OrderedDict()
_shared_generation_cache: dict[str, tuple[float, int]] = {}


def _bounded_put(cache: OrderedDict, key, value) -> None:
    """Store one LRU entry under the global hard bound."""

    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _MAX_CACHE:
        cache.popitem(last=False)


def _shared_generation(namespace: str, key: str, local: int) -> int:
    """Poll a cross-process generation counter under a short local TTL."""

    if not getattr(settings, "AUTHORIZATION_SHARED_INVALIDATION", True):
        return local
    cache_key = f"evennia:authgen:{namespace}:{key}"
    now = time.monotonic()
    cached = _shared_generation_cache.get(cache_key)
    interval = max(
        0.1,
        float(getattr(settings, "AUTHORIZATION_GENERATION_POLL_SECONDS", 2.0)),
    )
    if cached is not None and now - cached[0] < interval:
        return max(local, cached[1])
    try:
        shared = int(cache.get(cache_key, 0) or 0)
    except Exception:
        shared = local
    value = max(local, shared)
    _shared_generation_cache[cache_key] = (now, value)
    return value


def _publish_generation(namespace: str, key: str, value: int) -> int:
    """Publish invalidation without making authorization mutation depend on Redis."""

    if not getattr(settings, "AUTHORIZATION_SHARED_INVALIDATION", True):
        return value
    cache_key = f"evennia:authgen:{namespace}:{key}"
    try:
        cache.add(cache_key, 0, timeout=None)
        published = int(cache.incr(cache_key))
    except Exception:
        logger.log_trace("authorization shared invalidation publish failed")
        published = value
    _shared_generation_cache[cache_key] = (time.monotonic(), published)
    return published


def principal_refs(principal) -> tuple[str, ...]:
    """Return every legitimate authority identity for a principal.

    Account and puppet identities are both preserved. This supports legacy
    identity-lock migration without treating the object creator as an owner.
    """

    refs = []
    # Permissions follow the live driver, never a durable owner. Inspect the
    # class to avoid typeclass Attribute fallback for missing properties.
    if hasattr(type(principal), "puppeteer"):
        account = getattr(principal, "puppeteer", None)
    else:
        account = getattr(principal, "account", None)
    if account is not None and getattr(account, "pk", None) is not None:
        refs.append(f"account:{account.pk}")
    module = principal.__class__.__module__.lower()
    pk = getattr(principal, "pk", None) or getattr(principal, "id", None)
    if "account" in module and pk is not None:
        refs.append(f"account:{pk}")
    elif "session" in module and getattr(principal, "sessid", None) is not None:
        refs.append(f"session:{principal.sessid}")
    elif pk is not None:
        refs.append(f"object:{pk}")
    if pk is not None:
        refs.append(f"entity:{pk}")
    return tuple(dict.fromkeys(refs))


def resource_ref(resource) -> str:
    """Return a canonical generic reference for an authorization resource."""

    # Inspect the Python class, not the typeclass instance: missing-instance
    # attribute lookup may fall through to the Attribute table and turn a cache
    # invalidation into an unexpected database read.
    explicit = getattr(type(resource), "authorization_resource_ref", None)
    if callable(explicit):
        return str(explicit(resource))
    adapter = resource_adapters.for_resource(resource)
    if adapter is not None:
        return f"{adapter.kind}:{adapter.reference(resource)}"
    module = resource.__class__.__module__.lower()
    pk = getattr(resource, "pk", None) or getattr(resource, "id", None)
    if "command" in module or pk is None:
        cls = resource if isinstance(resource, type) else resource.__class__
        return f"command:{cls.__module__}.{cls.__qualname__}"
    if "account" in module:
        kind = "account"
    elif "comms" in module or "channel" in module:
        kind = "channel"
    elif "script" in module:
        kind = "script"
    elif "help" in module:
        kind = "help"
    else:
        kind = "object"
    return f"{kind}:{pk}"


def _legacy_permission_grants(principal) -> dict[str, set[tuple[str, str, str]]]:
    """Bridge legacy permission tags during the finite R3E migration."""

    result: dict[str, set[tuple[str, str, str]]] = {}
    sources = [(principal_refs(principal)[-1:] or ("anonymous",))[0], principal]
    sources = [sources]
    if hasattr(type(principal), "puppeteer"):
        account = getattr(principal, "puppeteer", None)
    else:
        account = getattr(principal, "account", None)
    if account is not None:
        sources.append((f"account:{account.pk}", account))
    for origin, source in sources:
        names = set()
        try:
            names.update(str(value).lower().rstrip("s") for value in source.permissions.all())
        except (AttributeError, TypeError):
            continue
        hierarchy = [str(value).lower().rstrip("s") for value in settings.PERMISSION_HIERARCHY]
        highest = max((hierarchy.index(name) for name in names if name in hierarchy), default=-1)
        names.update(hierarchy[: highest + 1])
        for name in names:
            key = f"legacy.permission.{name}"
            try:
                capability_registry.require(key)
            except Exception:
                continue
            result.setdefault(key, set()).add((origin, "world", "*"))
    return result


def load_grants(principal) -> GrantSnapshot:
    """Load or return cached positive grants for a principal."""

    refs = principal_refs(principal)
    cache_key = "|".join(refs) or "anonymous"
    generation = max(
        (_shared_generation("principal", ref, _principal_generation.get(ref, 0)) for ref in refs),
        default=0,
    )
    cached = _principal_cache.get(cache_key)
    now = timezone.now()
    if (
        cached is not None
        and cached.generation == generation
        and (cached.valid_until is None or cached.valid_until > now.timestamp())
    ):
        _principal_cache.move_to_end(cache_key)
        return cached
    query = AuthorizationGrant.objects.filter(
        principal_ref__in=refs,
        revoked_at__isnull=True,
    ).filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
    by_capability = _legacy_permission_grants(principal)
    valid_until = None
    for grant in query.only(
        "principal_ref",
        "capability",
        "scope_kind",
        "scope_key",
        "constraints",
        "expires_at",
    ):
        constraints = dict(grant.constraints or {})
        if set(constraints) - {"session_id"} or any(
            value is not None and not isinstance(value, (str, int, float, bool))
            for value in constraints.values()
        ):
            logger.log_err(
                f"Ignoring malformed authorization grant {grant.grant_id}: invalid constraints"
            )
            continue
        by_capability.setdefault(grant.capability, set()).add(
            GrantScope(
                grant.principal_ref,
                grant.scope_kind,
                grant.scope_key,
                tuple(sorted(constraints.items())),
            )
        )
        if grant.expires_at is not None:
            expiry = grant.expires_at.timestamp()
            valid_until = expiry if valid_until is None else min(valid_until, expiry)
    snapshot = GrantSnapshot(
        cache_key,
        {key: frozenset(values) for key, values in by_capability.items()},
        generation,
        valid_until,
    )
    _bounded_put(_principal_cache, cache_key, snapshot)
    return snapshot


def _computed_resource_labels(resource, ref: str) -> set[str]:
    """Compute cheap direct labels without walking containment graphs."""

    kind = ref.split(":", 1)[0]
    labels = {ref, f"kind:{kind}"}
    cls = resource.__class__
    labels.add(f"type:{cls.__module__}.{cls.__qualname__}".lower())
    adapter = resource_adapters.for_resource(resource)
    if adapter is not None:
        labels.update(str(label).strip().lower() for label in adapter.labels(resource))
    return labels


def load_resource(resource) -> ResourceSnapshot:
    """Load or return cached materialized labels for a resource."""

    ref = resource_ref(resource)
    generation = _shared_generation("resource", ref, _resource_generation.get(ref, 0))
    cached = _resource_cache.get(ref)
    if cached is not None and cached.generation == generation:
        _resource_cache.move_to_end(ref)
        return cached
    labels = _computed_resource_labels(resource, ref)
    labels.update(
        AuthorizationScopeLabel.objects.filter(resource_ref=ref).values_list("label", flat=True)
    )
    snapshot = ResourceSnapshot(ref, ref.split(":", 1)[0], frozenset(labels), generation)
    _bounded_put(_resource_cache, ref, snapshot)
    return snapshot


def bump_resource_generation(resource) -> None:
    """Invalidate only a resource-side scope/policy snapshot."""

    ref = resource_ref(resource)
    local = _resource_generation.get(ref, 0) + 1
    _resource_generation[ref] = _publish_generation("resource", ref, local)
    _resource_cache.pop(ref, None)
    for key in tuple(_policy_cache):
        if key[0] == ref:
            _policy_cache.pop(key, None)


def bump_principal_generation(principal_ref: str) -> None:
    """Invalidate grant snapshots affected by a principal mutation."""

    local = _principal_generation.get(principal_ref, 0) + 1
    _principal_generation[principal_ref] = _publish_generation("principal", principal_ref, local)
    _principal_cache.clear()
    _suspension_cache.clear()


def grant_capability(
    principal_ref: str,
    capability: str,
    *,
    scope_kind: str,
    scope_key: str,
    expires_at=None,
    provenance: str = "",
    actor_ref: str = "",
    reason: str = "",
    parent_grant_id: str = "",
    constraints: dict | None = None,
):
    """Create or refresh one positive capability grant atomically."""

    definition = capability_registry.require(capability)
    constraints = dict(constraints or {})
    unknown_constraints = set(constraints) - {"session_id"}
    if unknown_constraints:
        raise ValueError(f"unsupported grant constraints: {sorted(unknown_constraints)!r}")
    if any(
        value is not None and not isinstance(value, (str, int, float, bool))
        for value in constraints.values()
    ):
        raise ValueError("grant constraints must contain JSON scalar values")
    with transaction.atomic():
        AuthorizationPrincipalState.objects.get_or_create(principal_ref=principal_ref)
        AuthorizationPrincipalState.objects.select_for_update().get(principal_ref=principal_ref)
        existing = AuthorizationGrant.objects.filter(
            principal_ref=principal_ref,
            capability=definition.key,
            scope_kind=scope_kind,
            scope_key=scope_key,
            revoked_at__isnull=True,
        ).first()
        if existing is None:
            grant = AuthorizationGrant.objects.create(
                grant_id=uuid.uuid4().hex,
                principal_ref=principal_ref,
                capability=definition.key,
                scope_kind=scope_kind,
                scope_key=scope_key,
                expires_at=expires_at,
                provenance=provenance,
                parent_grant_id=parent_grant_id,
                constraints=constraints,
            )
        else:
            grant = existing
            grant.expires_at = expires_at
            grant.provenance = provenance
            grant.parent_grant_id = parent_grant_id
            grant.constraints = constraints
            grant.save(
                update_fields=[
                    "expires_at",
                    "provenance",
                    "parent_grant_id",
                    "constraints",
                    "updated_at",
                ]
            )
        AuthorizationAuditEvent.objects.create(
            event_id=uuid.uuid4().hex,
            kind="grant_created",
            principal_ref=principal_ref,
            capability=definition.key,
            resource_ref=scope_key if scope_kind == "resource" else "",
            actor_ref=actor_ref,
            reason=reason,
        )
        transaction.on_commit(lambda: bump_principal_generation(principal_ref))
    return grant


def delegate_grant(
    parent_grant_id: str,
    to_principal_ref: str,
    *,
    scope_kind: str,
    scope_key: str,
    expires_at=None,
    actor_ref: str = "",
    reason: str = "",
):
    """Delegate a bounded subset of an active grant.

    Delegation cannot change capability, outlive the parent, widen its scope,
    or use a capability marked non-delegable by the registry.
    """

    with transaction.atomic():
        parent = (
            AuthorizationGrant.objects.select_for_update()
            .filter(grant_id=parent_grant_id, revoked_at__isnull=True)
            .first()
        )
        if parent is None:
            raise ValueError("parent grant is missing or revoked")
        depth = 0
        ancestor_id = parent.parent_grant_id
        while ancestor_id:
            depth += 1
            if depth >= 8:
                raise ValueError("delegation depth exceeds the engine limit")
            ancestor_id = (
                AuthorizationGrant.objects.filter(grant_id=ancestor_id)
                .values_list("parent_grant_id", flat=True)
                .first()
                or ""
            )
        definition = capability_registry.require(parent.capability)
        if not definition.delegable:
            raise ValueError(f"capability {parent.capability} is not delegable")
        if parent.expires_at and (expires_at is None or expires_at > parent.expires_at):
            raise ValueError("delegated grant cannot outlive its parent")
        scope_is_subset = (parent.scope_kind, parent.scope_key) == ("world", "*") or (
            parent.scope_kind,
            parent.scope_key,
        ) == (scope_kind, scope_key)
        if not scope_is_subset:
            raise ValueError("delegated grant cannot widen its parent scope")
        return grant_capability(
            to_principal_ref,
            parent.capability,
            scope_kind=scope_kind,
            scope_key=scope_key,
            expires_at=expires_at,
            provenance="delegated",
            actor_ref=actor_ref,
            reason=reason,
            parent_grant_id=parent.grant_id,
        )


def revoke_grant(grant_id: str, *, actor_ref: str = "", reason: str = "") -> bool:
    """Revoke a grant and invalidate its immediate delegated children."""

    with transaction.atomic():
        grant = AuthorizationGrant.objects.select_for_update().filter(grant_id=grant_id).first()
        if grant is None or grant.revoked_at is not None:
            return False
        now = timezone.now()
        grant.revoked_at = now
        grant.save(update_fields=["revoked_at", "updated_at"])
        frontier = [grant_id]
        affected_principals = {grant.principal_ref}
        for _ in range(8):
            children = list(
                AuthorizationGrant.objects.filter(
                    parent_grant_id__in=frontier, revoked_at__isnull=True
                ).values_list("grant_id", "principal_ref")
            )
            if not children:
                break
            frontier = [child_id for child_id, _ in children]
            affected_principals.update(principal for _, principal in children)
            AuthorizationGrant.objects.filter(grant_id__in=frontier).update(revoked_at=now)
        AuthorizationAuditEvent.objects.create(
            event_id=uuid.uuid4().hex,
            kind="grant_revoked",
            principal_ref=grant.principal_ref,
            capability=grant.capability,
            actor_ref=actor_ref,
            reason=reason,
        )
        transaction.on_commit(
            lambda: [bump_principal_generation(ref) for ref in affected_principals]
        )
    return True


def issue_recovery_grant(account_id: int, *, reason: str, ttl_seconds: int = 900):
    """Issue a short-lived, audited break-glass grant from an offline operator."""

    ttl = max(60, min(int(ttl_seconds), 3600))
    return grant_capability(
        f"account:{int(account_id)}",
        "engine.authorization.break_glass",
        scope_kind="world",
        scope_key="*",
        expires_at=timezone.now() + timedelta(seconds=ttl),
        provenance="offline_recovery",
        actor_ref="deployment:console",
        reason=reason,
    )


def principal_is_suspended(principal) -> bool:
    """Return active universal suspension state for a principal."""

    refs = principal_refs(principal)
    cache_key = "|".join(refs) or "anonymous"
    generation = max(
        (_shared_generation("principal", ref, _principal_generation.get(ref, 0)) for ref in refs),
        default=0,
    )
    cached = _suspension_cache.get(cache_key)
    now = timezone.now()
    if (
        cached is not None
        and cached[0] == generation
        and (cached[2] is None or cached[2] > now.timestamp())
    ):
        _suspension_cache.move_to_end(cache_key)
        return cached[1]
    row = (
        AuthorizationPrincipalState.objects.filter(
            principal_ref__in=refs,
            suspended=True,
        )
        .filter(models.Q(suspended_until__isnull=True) | models.Q(suspended_until__gt=now))
        .first()
    )
    suspended = row is not None
    valid_until = (
        row.suspended_until.timestamp()
        if row is not None and row.suspended_until is not None
        else None
    )
    _bounded_put(_suspension_cache, cache_key, (generation, suspended, valid_until))
    return suspended


def set_principal_suspended(
    principal_ref: str,
    suspended: bool,
    *,
    reason: str,
    until=None,
    actor_ref: str = "",
):
    """Set universal suspension state and audit the mutation."""

    with transaction.atomic():
        state, _ = AuthorizationPrincipalState.objects.select_for_update().get_or_create(
            principal_ref=principal_ref
        )
        state.suspended = bool(suspended)
        state.reason = str(reason)[:255]
        state.suspended_until = until if suspended else None
        state.generation += 1
        state.save()
        AuthorizationAuditEvent.objects.create(
            event_id=uuid.uuid4().hex,
            kind="principal_suspended" if suspended else "principal_reinstated",
            principal_ref=principal_ref,
            actor_ref=actor_ref,
            reason=reason,
        )
        transaction.on_commit(lambda: bump_principal_generation(principal_ref))
    return state


def set_scope_labels(resource, labels, *, source: str = "authored") -> None:
    """Replace labels from one source and invalidate the resource cache."""

    ref = resource_ref(resource)
    normalized = {str(label).strip().lower() for label in labels if str(label).strip()}
    with transaction.atomic():
        AuthorizationScopeLabel.objects.filter(resource_ref=ref, source=source).exclude(
            label__in=normalized
        ).delete()
        for label in normalized:
            AuthorizationScopeLabel.objects.update_or_create(
                resource_ref=ref,
                label=label,
                defaults={"source": source},
            )
        transaction.on_commit(lambda: bump_resource_generation(resource))


def load_policy(resource, access_type: str):
    """Load a sparse compiled instance policy, or return ``None``."""

    ref = resource_ref(resource)
    key = (ref, str(access_type).lower())
    if key in _policy_cache:
        _policy_cache.move_to_end(key)
        return _policy_cache[key]
    row = AuthorizationPolicyOverride.objects.filter(resource_ref=ref, access_type=key[1]).first()
    policy = (
        policy_from_data(row.policy)
        if row and row.policy
        else policy_registry.resolve(resource, ref.split(":", 1)[0], key[1])
    )
    _bounded_put(_policy_cache, key, policy)
    return policy


def clear_authorization_caches() -> None:
    """Clear runtime caches for reloads and deterministic tests."""

    _principal_cache.clear()
    _resource_cache.clear()
    _policy_cache.clear()
    _suspension_cache.clear()
    _shared_generation_cache.clear()
