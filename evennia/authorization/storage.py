"""Persistent grants, scopes, policies, and split authorization caches."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from datetime import timedelta
from hashlib import sha256

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
_policy_package_cache: OrderedDict[str, tuple[int, dict[str, object]]] = OrderedDict()
_suspension_cache: OrderedDict[str, tuple[int, bool, float | None]] = OrderedDict()
_shared_generation_cache: dict[str, tuple[float, int]] = {}


def _bounded_put(cache: OrderedDict, key, value) -> None:
    """Store one LRU entry under the global hard bound."""

    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _MAX_CACHE:
        cache.popitem(last=False)


def _generation_cache_key(namespace: str, key: str) -> str:
    """Return a deterministic generation key safe for every Django backend."""

    digest = sha256(str(key).encode("utf-8")).hexdigest()
    return f"evennia:authgen:{namespace}:{digest}"


def _shared_generation(namespace: str, key: str, local: int) -> int:
    """Poll a cross-process generation counter under a short local TTL."""

    if not getattr(settings, "AUTHORIZATION_SHARED_INVALIDATION", True):
        return local
    cache_key = _generation_cache_key(namespace, key)
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
    cache_key = _generation_cache_key(namespace, key)
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

    Account and puppet identities are both preserved without treating object
    creation as ownership.
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


#: Attribute document keys for the quell flag. Read directly from the column
#: when the handler is unavailable; see :func:`_quell_flag`.
_QUELL_CATEGORY = "~"
_QUELL_DATA = "_d"
_QUELL_KEY = "_quell"


def _read_quell_document(source) -> bool:
    """Return the quell flag from the principal's stored attribute document.

    Readable from any thread, because it is an ordinary column on the
    principal's own row rather than handler state.

    Staleness contract: this sees the last flushed document, so a quell set
    within the current ``ATTRIBUTE_FLUSH_INTERVAL`` (one second by default)
    may not be visible yet. Acceptable because quell is a voluntary,
    self-imposed suppression rather than a revocation, and because the
    alternative -- denying every capability whenever the reader is not the IO
    owner -- is not fail-closed, it is fail-broken.
    """

    pk = getattr(source, "pk", None)
    if pk is None:
        return False
    try:
        row = type(source)._base_manager.filter(pk=pk).values_list("db_attrs", flat=True).first()
    except Exception:  # noqa: BLE001 - a failed read must not deny authority
        return False
    if not isinstance(row, dict):
        return False
    section = row.get(_QUELL_CATEGORY)
    if not isinstance(section, dict):
        return False
    data = section.get(_QUELL_DATA)
    if not isinstance(data, dict):
        return False
    return bool(data.get(_QUELL_KEY))


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
    by_capability: dict[str, set[GrantScope]] = {}
    if hasattr(type(principal), "puppeteer"):
        authority_source = getattr(principal, "puppeteer", None) or principal
    else:
        authority_source = getattr(principal, "account", None) or principal
    try:
        authority_suppressed = bool(authority_source.attributes.get("_quell"))
    except AttributeError:
        authority_suppressed = False
    except Exception:  # noqa: BLE001 - the handler is IO-owner-only
        # Off the IO thread, building or reading the attribute handler raises.
        # That exception used to travel into has_capability's blanket except
        # and become "holds no capabilities at all", so every authorization
        # decision made from a worker thread failed for a reason unrelated to
        # authorization. Read the stored document instead.
        authority_suppressed = _read_quell_document(authority_source)
    valid_until = None
    for grant in query.only(
        "principal_ref",
        "capability",
        "scope_kind",
        "scope_key",
        "constraints",
        "expires_at",
    ):
        if authority_suppressed:
            continue
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
    _policy_package_cache.pop(ref, None)


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
        bump_principal_generation(principal_ref)
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
        for principal in affected_principals:
            bump_principal_generation(principal)
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
        (
            state,
            _,
        ) = AuthorizationPrincipalState.objects.select_for_update().get_or_create(
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
        bump_principal_generation(principal_ref)
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
        bump_resource_generation(resource)
        transaction.on_commit(lambda: bump_resource_generation(resource))


def load_policy(resource, access_type: str):
    """Load one policy from a generation-aware, resource-wide package.

    One cold indexed query loads every instance override for the resource.
    Missing operations are negative-cached with that package and resolve only
    against immutable registry templates, so steady-state checks perform no
    authorization SQL.
    """

    ref = resource_ref(resource)
    access_key = str(access_type).lower()
    generation = _shared_generation("resource", ref, _resource_generation.get(ref, 0))
    cached = _policy_package_cache.get(ref)
    if cached is not None and cached[0] == generation:
        _policy_package_cache.move_to_end(ref)
        overrides = cached[1]
    else:
        overrides = {
            str(operation).lower(): policy_from_data(data)
            for operation, data in AuthorizationPolicyOverride.objects.filter(
                resource_ref=ref
            ).values_list("access_type", "policy")
            if data
        }
        _bounded_put(_policy_package_cache, ref, (generation, overrides))
    if access_key in overrides:
        return overrides[access_key]
    return policy_registry.resolve(resource, ref.split(":", 1)[0], access_key)


def preload_policy_packages(resources) -> int:
    """Batch-warm instance policy packages for a bounded resource collection.

    Command-set warmup uses this to replace one cold query per command class
    with one indexed ``resource_ref__in`` query. Existing generation-valid
    packages are skipped. Registry defaults remain immutable and need no rows.
    """

    by_ref = {resource_ref(resource): resource for resource in resources}
    pending: dict[str, int] = {}
    for ref in by_ref:
        generation = _shared_generation("resource", ref, _resource_generation.get(ref, 0))
        cached = _policy_package_cache.get(ref)
        if cached is None or cached[0] != generation:
            pending[ref] = generation
    if not pending:
        return 0

    grouped: dict[str, dict[str, object]] = {ref: {} for ref in pending}
    rows = AuthorizationPolicyOverride.objects.filter(resource_ref__in=pending).values_list(
        "resource_ref", "access_type", "policy"
    )
    for ref, operation, data in rows:
        if data:
            grouped[ref][str(operation).lower()] = policy_from_data(data)
    for ref, generation in pending.items():
        _bounded_put(_policy_package_cache, ref, (generation, grouped[ref]))
    return len(pending)


def clear_authorization_caches() -> None:
    """Clear runtime caches for reloads and deterministic tests."""

    _principal_cache.clear()
    _resource_cache.clear()
    _policy_package_cache.clear()
    _suspension_cache.clear()
    _shared_generation_cache.clear()
