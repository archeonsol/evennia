"""Persistent grants, scopes, policies, and split authorization caches."""

from __future__ import annotations

import asyncio
import contextvars
import re
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256

from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.utils import timezone

from evennia.server.models import (
    AuthorizationAuditEvent,
    AuthorizationGrant,
    AuthorizationGroupMembership,
    AuthorizationPolicyBundle,
    AuthorizationPolicyOverride,
    AuthorizationPrincipalGroup,
    AuthorizationPrincipalState,
    AuthorizationScopeLabel,
)
from evennia.utils import logger
from evennia.utils.utils import cached_setting

from . import invalidation
from .capabilities import capability_registry
from .engine import GrantScope, GrantSnapshot, ResourceSnapshot
from .policy import (
    grant_constraint_keys,
    policy_from_data,
    policy_registry,
    validate_grant_constraints,
)
from .resources import resource_adapters

_MAX_CACHE = 20_000
_principal_generation: dict[str, int] = {}
_resource_generation: dict[str, int] = {}
_principal_cache: OrderedDict[str, GrantSnapshot] = OrderedDict()
_resource_cache: OrderedDict[str, ResourceSnapshot] = OrderedDict()
_policy_package_cache: OrderedDict[str, tuple[int, dict[str, object]]] = OrderedDict()
_suspension_cache: OrderedDict[str, tuple[int, bool, float | None]] = OrderedDict()
_shared_generation_cache: dict[str, tuple[float, int]] = {}
_policy_bundle_cache: tuple[int, int, dict] | None = None
_policy_bundle_generation = 0
_NO_TASK = object()
_snapshot_scope_owner = contextvars.ContextVar("evennia_authorization_snapshot_owner", default=None)
_prewarm_task = None
_prewarm_loop = None
_pending_prewarm_principals = {}
_pending_prewarm_resources = {}
_SNAPSHOT_MISS_WARN_INTERVAL = 60.0
_snapshot_miss_warned_at: dict[str, float] = {}


class AuthorizationSnapshotUnavailable(RuntimeError):
    """A managed action attempted authorization without a local snapshot."""


@contextmanager
def authorization_snapshot_scope():
    """Require authorization reads on this task to use local snapshots.

    The scope is bound to the exact task (or the no-task synchronous context)
    that entered it. Application ContextVars are copied into detached runtime
    roots and child callbacks (see ``clock._isolated_database_context``), so a
    scope flag alone would silently propagate the no-I/O requirement into work
    that outlives the action and lacks its prewarmed snapshots.
    """

    try:
        owner = asyncio.current_task()
    except RuntimeError:
        owner = None
    token = _snapshot_scope_owner.set(owner if owner is not None else _NO_TASK)
    try:
        yield
    finally:
        _snapshot_scope_owner.reset(token)


def _snapshot_scope_active() -> bool:
    """Return whether this exact task is inside an authorization snapshot scope."""

    owner = _snapshot_scope_owner.get()
    if owner is None:
        return False
    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    if owner is _NO_TASK:
        return current is None
    return current is owner


def _note_snapshot_miss(kind: str, key: str) -> None:
    """Record a covered-path miss and warn at a bounded cadence.

    A miss means rule evaluation touched a principal or resource that the
    prewarm did not cover (or could not refresh). The read falls back to the
    ordinary inline query unless ``AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR`` asks
    for the strict tripwire.
    """

    try:
        from evennia.server.prometheus_metrics import record_authorization_snapshot_miss

        record_authorization_snapshot_miss(kind)
    except Exception:
        pass
    now = time.monotonic()
    last = _snapshot_miss_warned_at.get(kind)
    if last is not None and now - last < _SNAPSHOT_MISS_WARN_INTERVAL:
        return
    _snapshot_miss_warned_at[kind] = now
    logger.log_warn(
        f"authorization snapshot miss ({kind}): {key}; "
        "falling back to an inline read for this evaluation"
    )


def _bounded_put(cache: OrderedDict, key, value) -> None:
    """Store one LRU entry under the global hard bound."""

    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _MAX_CACHE:
        cache.popitem(last=False)


_GENERATION_KEY_CACHE: dict[tuple[str, str], str] = {}
_GENERATION_KEY_CACHE_MAX = 4096


def _generation_cache_key(namespace: str, key: str) -> str:
    """Return a deterministic generation key safe for every Django backend.

    The digest is memoized per ``(namespace, key)``: the same fact is looked up
    on every authorization evaluation, and the sha256 was measurable reactor
    work in a 100-bot profile. The cache is bounded; clearing on overflow keeps
    it flat with no LRU bookkeeping on the hot path.
    """

    cache_key = (namespace, key)
    cached = _GENERATION_KEY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    digest = sha256(str(key).encode("utf-8")).hexdigest()
    result = f"evennia:authgen:{namespace}:{digest}"
    if len(_GENERATION_KEY_CACHE) >= _GENERATION_KEY_CACHE_MAX:
        _GENERATION_KEY_CACHE.clear()
    _GENERATION_KEY_CACHE[cache_key] = result
    return result


def _shared_generation(namespace: str, key: str, local: int) -> int:
    """Return the newest generation this process knows for one fact.

    With push invalidation the value is authoritative until an event drops it,
    so this is a local dictionary read with no TTL and no I/O. Without push it
    keeps the original bounded Redis polling behavior.
    """

    if not cached_setting("AUTHORIZATION_SHARED_INVALIDATION", True):
        return local
    cache_key = _generation_cache_key(namespace, key)
    cached = _shared_generation_cache.get(cache_key)
    if invalidation.push_enabled():
        return max(local, cached[1] if cached is not None else 0)
    now = time.monotonic()
    interval = max(
        0.1,
        float(cached_setting("AUTHORIZATION_GENERATION_POLL_SECONDS", 2.0)),
    )
    if cached is not None and now - cached[0] < interval:
        return max(local, cached[1])
    if _snapshot_scope_active():
        # The action bridge refreshes generations and snapshots before entering
        # this scope. Never turn an expired poll TTL into reactor-thread Redis
        # I/O midway through synchronous rule evaluation.
        return max(local, cached[1] if cached is not None else local)
    try:
        shared = int(cache.get(cache_key, 0) or 0)
    except Exception:
        shared = local
    value = max(local, shared)
    _shared_generation_cache[cache_key] = (now, value)
    return value


def _publish_generation_io(cache_key: str, namespace=None, key=None, value=None) -> int:
    """Publish one shared counter (and event) in a worker-safe callable."""

    cache.add(cache_key, 0, timeout=None)
    published = int(cache.incr(cache_key))
    if namespace is None or key is None:
        return published
    final = max(int(value or 0), published)
    invalidation.publish_generation(namespace, key, final)
    return final


def _publish_generation(namespace: str, key: str, value: int) -> int:
    """Publish invalidation without making authorization mutation depend on Redis."""

    if not cached_setting("AUTHORIZATION_SHARED_INVALIDATION", True):
        return value
    cache_key = _generation_cache_key(namespace, key)
    cached = _shared_generation_cache.get(cache_key)
    _shared_generation_cache[cache_key] = (
        time.monotonic(),
        max(value, cached[1] if cached is not None else value),
    )
    if invalidation.push_enabled():
        invalidation.start()
    try:
        from evennia.utils import clock, defer

        if clock.loop_running() and clock.is_io_owner():
            defer.background(_publish_generation_io, cache_key, namespace, key, value)
            return value
    except Exception:
        logger.log_trace("authorization async invalidation scheduling failed")
    try:
        published = _publish_generation_io(cache_key, namespace, key, value)
    except Exception:
        logger.log_trace("authorization shared invalidation publish failed")
        return value
    _shared_generation_cache[cache_key] = (time.monotonic(), max(value, published))
    return max(value, published)


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


def _authority_source(principal):
    """Return the object whose attribute document carries the quell flag."""

    if hasattr(type(principal), "puppeteer"):
        return getattr(principal, "puppeteer", None) or principal
    return getattr(principal, "account", None) or principal


def _document_quell(document) -> bool:
    """Return the quell flag from a raw JSONB attribute document."""

    if not isinstance(document, dict):
        return False
    section = document.get(_QUELL_CATEGORY)
    if not isinstance(section, dict):
        return False
    data = section.get(_QUELL_DATA)
    if not isinstance(data, dict):
        return False
    return bool(data.get(_QUELL_KEY))


def _read_quell_document(source) -> bool:
    """Return the quell flag from the principal's stored attribute document.

    Readable from any thread, because it is an ordinary column on the
    principal's own row rather than handler state.

    Staleness contract: this sees the last flushed document, so a quell set
    within the current ``ATTRIBUTE_FLUSH_INTERVAL`` (one second by default) may
    not be visible yet. Acceptable because quell is a voluntary, self-imposed
    suppression rather than a revocation, and because the alternative -- denying
    every capability whenever the reader is not the IO owner -- is not
    fail-closed, it is fail-broken.
    """

    pk = getattr(source, "pk", None)
    if pk is None:
        return False
    try:
        row = type(source)._base_manager.filter(pk=pk).values_list("db_attrs", flat=True).first()
    except Exception:  # noqa: BLE001 - a failed read must not deny authority
        return False
    return _document_quell(row)


def _principal_group_refs(refs) -> tuple[str, ...]:
    """Return the ``group:<ref>`` grant subjects one principal belongs to."""

    if not refs:
        return ()
    return tuple(
        sorted(
            f"group:{group_ref}"
            for group_ref in AuthorizationGroupMembership.objects.filter(
                principal_ref__in=refs
            ).values_list("group_ref", flat=True)
        )
    )


def load_grants(principal) -> GrantSnapshot:
    """Load or return cached positive grants for a principal."""

    refs = principal_refs(principal)
    cache_key = "|".join(refs) or "anonymous"
    cached = _principal_cache.get(cache_key)
    # Group subjects are part of the generation check so a grant edited on a
    # group invalidates every member's cached snapshot.
    generation_refs = list(refs)
    if cached is not None:
        generation_refs.extend(cached.group_refs)
    generation = max(
        (
            _shared_generation("principal", ref, _principal_generation.get(ref, 0))
            for ref in generation_refs
        ),
        default=0,
    )
    now = timezone.now()
    if (
        cached is not None
        and cached.generation == generation
        and (cached.valid_until is None or cached.valid_until > now.timestamp())
    ):
        _principal_cache.move_to_end(cache_key)
        return cached
    if _snapshot_scope_active():
        if cached_setting("AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR", False):
            raise AuthorizationSnapshotUnavailable(
                f"principal snapshot unavailable for {cache_key}"
            )
        _note_snapshot_miss("principal", cache_key)
    group_refs = _principal_group_refs(refs)
    if group_refs:
        group_generations = [
            _shared_generation("principal", ref, _principal_generation.get(ref, 0))
            for ref in group_refs
        ]
        generation = max([generation, *group_generations])
    query = AuthorizationGrant.objects.filter(
        principal_ref__in=[*refs, *group_refs],
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
        if set(constraints) - grant_constraint_keys() or any(
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
        frozenset(group_refs),
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
    if _snapshot_scope_active():
        if cached_setting("AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR", False):
            raise AuthorizationSnapshotUnavailable(f"resource snapshot unavailable for {ref}")
        _note_snapshot_miss("resource", ref)
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


def _note_move_invalidation(outcome: str) -> None:
    """Record one move-invalidation decision without touching the hot path."""

    try:
        from evennia.server.prometheus_metrics import record_authorization_move_invalidation

        record_authorization_move_invalidation(outcome)
    except Exception:
        pass


def bump_resource_generation_after_move(resource) -> bool:
    """Invalidate moved-resource facts only for location-derived adapter labels.

    An adapter lookup failure fails safe by invalidating: over-invalidation
    costs one refetch, while a missed location-derived label change would serve
    a stale snapshot.

    Returns:
        bool: Whether invalidation ran (location-sensitive or lookup failed).

    """

    try:
        adapter = resource_adapters.for_resource(resource)
        location_sensitive = adapter is not None and adapter.location_sensitive
    except Exception:
        logger.log_trace("authorization move invalidation adapter lookup failed")
        location_sensitive = True
        _note_move_invalidation("lookup_error")
    else:
        if not location_sensitive:
            _note_move_invalidation("skipped")
            return False
        _note_move_invalidation("bumped")
    bump_resource_generation(resource)
    return True


def bump_principal_generation(principal_ref: str) -> None:
    """Invalidate grant snapshots affected by a principal mutation."""

    local = _principal_generation.get(principal_ref, 0) + 1
    _principal_generation[principal_ref] = _publish_generation("principal", principal_ref, local)
    _principal_cache.clear()
    _suspension_cache.clear()


def _validated_grant_constraints(constraints: dict | None) -> dict:
    """Return a copy of `constraints`, rejecting anything the engine cannot enforce.

    Args:
        constraints (dict or None): Caller-supplied grant constraints.

    Returns:
        dict: A defensive copy, empty when none were given.

    Raises:
        ValueError: If an unsupported key or a non-scalar value is present.

    """
    return validate_grant_constraints(constraints)


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
    constraints = _validated_grant_constraints(constraints)
    if principal_ref.startswith("group:"):
        group_ref = principal_ref.split(":", 1)[1]
        if not AuthorizationPrincipalGroup.objects.filter(group_ref=group_ref).exists():
            raise ValueError(f"unknown authorization group {group_ref!r}")
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


def grant_capabilities(
    principal_ref: str,
    capabilities,
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
    """Create or refresh many capability grants for one principal, atomically.

    Args:
        principal_ref (str): The principal receiving every grant.
        capabilities (iterable): Capability keys or aliases. Each is resolved
            through the registry, and duplicates collapse to a single grant.
        scope_kind (str): Scope kind shared by all the grants.
        scope_key (str): Scope key shared by all the grants.
        expires_at (datetime, optional): Shared expiry, or `None` for none.
        provenance (str, optional): Shared provenance marker.
        actor_ref (str, optional): Who performed the grant, for the audit trail.
        reason (str, optional): Free-text audit reason.
        parent_grant_id (str, optional): Shared delegation parent.
        constraints (dict, optional): Shared constraints; see
            `_validated_grant_constraints`.

    Returns:
        list: The resulting `AuthorizationGrant` rows, in the order the
            capabilities resolved, with duplicates removed.

    Notes:
        Semantically identical to calling `grant_capability` once per
        capability, but at a fixed query cost rather than six queries each.
        Granting a whole bundle is the normal case (staff promotion and the
        test fixtures both do it, and `runtime_operator` alone is 57
        capabilities), so the loop dominated every caller that used it.

        The registry is consulted for every capability before anything is
        written, so an unknown capability rejects the whole call instead of
        leaving a half-granted principal behind.

    """
    keys = []
    seen = set()
    for capability in capabilities:
        key = capability_registry.require(capability).key
        if key not in seen:
            seen.add(key)
            keys.append(key)
    if not keys:
        return []
    constraints = _validated_grant_constraints(constraints)
    with transaction.atomic():
        AuthorizationPrincipalState.objects.get_or_create(principal_ref=principal_ref)
        AuthorizationPrincipalState.objects.select_for_update().get(principal_ref=principal_ref)
        existing = {
            grant.capability: grant
            for grant in AuthorizationGrant.objects.filter(
                principal_ref=principal_ref,
                capability__in=keys,
                scope_kind=scope_kind,
                scope_key=scope_key,
                revoked_at__isnull=True,
            )
        }
        created = []
        refreshed = []
        # bulk_update reads the attribute rather than calling pre_save, so an
        # auto_now column keeps its old value unless it is set explicitly here.
        now = timezone.now()
        for key in keys:
            grant = existing.get(key)
            if grant is None:
                created.append(
                    AuthorizationGrant(
                        grant_id=uuid.uuid4().hex,
                        principal_ref=principal_ref,
                        capability=key,
                        scope_kind=scope_kind,
                        scope_key=scope_key,
                        expires_at=expires_at,
                        provenance=provenance,
                        parent_grant_id=parent_grant_id,
                        constraints=constraints,
                    )
                )
            else:
                grant.expires_at = expires_at
                grant.provenance = provenance
                grant.parent_grant_id = parent_grant_id
                grant.constraints = constraints
                grant.updated_at = now
                refreshed.append(grant)
        if created:
            AuthorizationGrant.objects.bulk_create(created)
        if refreshed:
            AuthorizationGrant.objects.bulk_update(
                refreshed,
                [
                    "expires_at",
                    "provenance",
                    "parent_grant_id",
                    "constraints",
                    "updated_at",
                ],
            )
        AuthorizationAuditEvent.objects.bulk_create(
            [
                AuthorizationAuditEvent(
                    event_id=uuid.uuid4().hex,
                    kind="grant_created",
                    principal_ref=principal_ref,
                    capability=key,
                    resource_ref=scope_key if scope_kind == "resource" else "",
                    actor_ref=actor_ref,
                    reason=reason,
                )
                for key in keys
            ]
        )
        bump_principal_generation(principal_ref)
        transaction.on_commit(lambda: bump_principal_generation(principal_ref))
    by_key = {grant.capability: grant for grant in created}
    by_key.update({grant.capability: grant for grant in refreshed})
    return [by_key[key] for key in keys]


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
    grants = _principal_cache.get(cache_key)
    generation_refs = list(refs)
    if grants is not None:
        generation_refs.extend(grants.group_refs)
    generation = max(
        (
            _shared_generation("principal", ref, _principal_generation.get(ref, 0))
            for ref in generation_refs
        ),
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
    if _snapshot_scope_active():
        if cached_setting("AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR", False):
            raise AuthorizationSnapshotUnavailable(
                f"suspension snapshot unavailable for {cache_key}"
            )
        _note_snapshot_miss("suspension", cache_key)
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


_GROUP_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _normalize_group_ref(group_ref: str) -> str:
    """Normalize and validate one group reference."""

    normalized = str(group_ref or "").strip().lower()
    if not _GROUP_REF_RE.match(normalized):
        raise ValueError(f"invalid authorization group reference {group_ref!r}")
    return normalized


def create_principal_group(
    group_ref: str,
    *,
    label: str = "",
    category: str = "",
    metadata: dict | None = None,
    actor_ref: str = "",
    reason: str = "",
):
    """Create or refresh one principal group."""

    normalized = _normalize_group_ref(group_ref)
    with transaction.atomic():
        group, _created = AuthorizationPrincipalGroup.objects.update_or_create(
            group_ref=normalized,
            defaults={
                "label": str(label or ""),
                "category": str(category or "").strip().lower(),
                "metadata": dict(metadata or {}),
            },
        )
        AuthorizationAuditEvent.objects.create(
            event_id=uuid.uuid4().hex,
            kind="group_created",
            principal_ref=f"group:{normalized}",
            actor_ref=actor_ref,
            reason=reason,
        )
    return group


def delete_principal_group(group_ref: str, *, actor_ref: str = "", reason: str = "") -> int:
    """Delete one group, its memberships, and its grants; return member count."""

    normalized = _normalize_group_ref(group_ref)
    with transaction.atomic():
        members = list(
            AuthorizationGroupMembership.objects.filter(group_ref=normalized).values_list(
                "principal_ref", flat=True
            )
        )
        AuthorizationGroupMembership.objects.filter(group_ref=normalized).delete()
        AuthorizationPrincipalGroup.objects.filter(group_ref=normalized).delete()
        # Group-targeted grants are revoked so a later group with the same ref
        # cannot resurrect authority that was deliberately removed.
        AuthorizationGrant.objects.filter(
            principal_ref=f"group:{normalized}", revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        AuthorizationAuditEvent.objects.create(
            event_id=uuid.uuid4().hex,
            kind="group_deleted",
            principal_ref=f"group:{normalized}",
            actor_ref=actor_ref,
            reason=reason,
            data={"members": len(members)},
        )
        for member in members:
            bump_principal_generation(member)
        transaction.on_commit(lambda: [bump_principal_generation(member) for member in members])
    return len(members)


def join_principal_group(
    principal_ref: str,
    group_ref: str,
    *,
    provenance: str = "",
    actor_ref: str = "",
    reason: str = "",
) -> bool:
    """Add one principal to a group and invalidate its grant snapshot."""

    normalized = _normalize_group_ref(group_ref)
    if not AuthorizationPrincipalGroup.objects.filter(group_ref=normalized).exists():
        raise ValueError(f"unknown authorization group {group_ref!r}")
    with transaction.atomic():
        _membership, created = AuthorizationGroupMembership.objects.get_or_create(
            group_ref=normalized,
            principal_ref=principal_ref,
            defaults={"provenance": provenance},
        )
        if created:
            AuthorizationAuditEvent.objects.create(
                event_id=uuid.uuid4().hex,
                kind="group_joined",
                principal_ref=principal_ref,
                resource_ref=f"group:{normalized}",
                actor_ref=actor_ref,
                reason=reason,
            )
            bump_principal_generation(principal_ref)
            transaction.on_commit(lambda: bump_principal_generation(principal_ref))
    return created


def leave_principal_group(
    principal_ref: str, group_ref: str, *, actor_ref: str = "", reason: str = ""
) -> bool:
    """Remove one principal from a group and invalidate its grant snapshot."""

    normalized = _normalize_group_ref(group_ref)
    with transaction.atomic():
        deleted, _detail = AuthorizationGroupMembership.objects.filter(
            group_ref=normalized, principal_ref=principal_ref
        ).delete()
        if deleted:
            AuthorizationAuditEvent.objects.create(
                event_id=uuid.uuid4().hex,
                kind="group_left",
                principal_ref=principal_ref,
                resource_ref=f"group:{normalized}",
                actor_ref=actor_ref,
                reason=reason,
            )
            bump_principal_generation(principal_ref)
            transaction.on_commit(lambda: bump_principal_generation(principal_ref))
    return bool(deleted)


def principal_groups(principal_ref: str) -> tuple[str, ...]:
    """Return the bare group refs one principal belongs to."""

    return tuple(
        sorted(
            AuthorizationGroupMembership.objects.filter(principal_ref=principal_ref).values_list(
                "group_ref", flat=True
            )
        )
    )


def _policy_generation() -> int:
    """Return the active policy-bundle generation."""

    return _shared_generation("policy", "*", _policy_bundle_generation)


def active_policy_bundle() -> tuple[int, dict | None]:
    """Return the active compiled policy document, cached by generation.

    Bundles are opt-in: while no bundle is active this returns
    ``(0, None)`` and callers read live ``AuthorizationPolicyOverride`` rows,
    exactly as before. Once a bundle is activated it is authoritative for
    instance policies until another bundle (or none) is activated.

    Returns:
        tuple: ``(version, document)`` where ``document`` maps
        ``resource_ref -> {access_type: policy_data}``, or ``None`` when no
        bundle is active. Registry templates remain code and need no rows.

    """

    global _policy_bundle_cache
    generation = _policy_generation()
    cached = _policy_bundle_cache
    if cached is not None and cached[0] == generation:
        return cached[1], cached[2]
    row = (
        AuthorizationPolicyBundle.objects.filter(active=True)
        .order_by("-version")
        .values_list("version", "document")
        .first()
    )
    if row is None:
        version, document = 0, None
    else:
        version, document = row[0], row[1] or {}
    _policy_bundle_cache = (generation, version, document)
    return version, document


def _bundle_overrides(document, ref: str) -> dict[str, dict] | None:
    """Return one resource's bundle overrides, or ``None`` for table mode."""

    if document is None:
        return None
    return document.get(ref) or {}


def compile_policy_bundle(
    *,
    provenance: str = "",
    actor_ref: str = "",
    reason: str = "",
    activate: bool = True,
) -> int:
    """Compile every instance policy override into a new bundle version.

    The compiled document is validated with the same fail-closed compiler the
    evaluator uses, so a malformed policy can never reach a live bundle.

    Returns:
        int: The new bundle version.

    """

    document: dict[str, dict[str, dict]] = {}
    rows = AuthorizationPolicyOverride.objects.exclude(policy={}).values_list(
        "resource_ref", "access_type", "policy"
    )
    for resource_ref_value, access_type, policy_data in rows:
        if not policy_data:
            continue
        # Fail closed: a bundle that cannot compile is never activated.
        policy_from_data(policy_data)
        document.setdefault(resource_ref_value, {})[str(access_type).lower()] = policy_data
    with transaction.atomic():
        latest = (
            AuthorizationPolicyBundle.objects.order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        version = int(latest or 0) + 1
        AuthorizationPolicyBundle.objects.create(
            version=version,
            document=document,
            active=False,
            provenance=provenance,
        )
        AuthorizationAuditEvent.objects.create(
            event_id=uuid.uuid4().hex,
            kind="policy_bundle_compiled",
            actor_ref=actor_ref,
            reason=reason,
            data={"version": version, "resources": len(document)},
        )
        if activate:
            _activate_policy_bundle_locked(version, actor_ref=actor_ref, reason=reason)
    return version


def activate_policy_bundle(version: int, *, actor_ref: str = "", reason: str = "") -> bool:
    """Atomically switch evaluation to one compiled bundle version."""

    with transaction.atomic():
        if not AuthorizationPolicyBundle.objects.filter(version=int(version)).exists():
            raise ValueError(f"unknown authorization policy bundle {version!r}")
        _activate_policy_bundle_locked(int(version), actor_ref=actor_ref, reason=reason)
    return True


def _activate_policy_bundle_locked(version: int, *, actor_ref: str, reason: str) -> None:
    """Owner transaction: switch the active flag and publish the generation."""

    AuthorizationPolicyBundle.objects.exclude(version=version).filter(active=True).update(
        active=False
    )
    AuthorizationPolicyBundle.objects.filter(version=version).update(active=True)
    AuthorizationAuditEvent.objects.create(
        event_id=uuid.uuid4().hex,
        kind="policy_bundle_activated",
        actor_ref=actor_ref,
        reason=reason,
        data={"version": version},
    )
    _bump_policy_generation()
    transaction.on_commit(_bump_policy_generation)


def _bump_policy_generation() -> None:
    """Invalidate every process's policy packages and bundle cache."""

    global _policy_bundle_generation, _policy_bundle_cache
    _policy_bundle_generation += 1
    _policy_bundle_generation = _publish_generation("policy", "*", _policy_bundle_generation)
    _policy_package_cache.clear()
    _policy_bundle_cache = None


def load_policy(resource, access_type: str):
    """Load one policy from the active bundle and registry defaults.

    Instance overrides come from the active compiled bundle; missing
    operations resolve against immutable registry templates, so steady-state
    checks perform no authorization SQL.
    """

    ref = resource_ref(resource)
    access_key = str(access_type).lower()
    generation = max(
        _shared_generation("resource", ref, _resource_generation.get(ref, 0)),
        _policy_generation(),
    )
    cached = _policy_package_cache.get(ref)
    if cached is not None and cached[0] == generation:
        _policy_package_cache.move_to_end(ref)
        overrides = cached[1]
    else:
        if _snapshot_scope_active():
            if cached_setting("AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR", False):
                raise AuthorizationSnapshotUnavailable(f"policy snapshot unavailable for {ref}")
            _note_snapshot_miss("policy", ref)
        _version, document = active_policy_bundle()
        bundle_overrides = _bundle_overrides(document, ref)
        if bundle_overrides is None:
            rows = AuthorizationPolicyOverride.objects.filter(resource_ref=ref).values_list(
                "access_type", "policy"
            )
        else:
            rows = bundle_overrides.items()
        overrides = {
            str(operation).lower(): policy_from_data(data) for operation, data in rows if data
        }
        _bounded_put(_policy_package_cache, ref, (generation, overrides))
    if access_key in overrides:
        return overrides[access_key]
    return policy_registry.resolve(resource, ref.split(":", 1)[0], access_key)


def preload_policy_packages(resources) -> int:
    """Batch-warm instance policy packages for a bounded resource collection.

    Command-set warmup uses this to replace one cold per-command lookup with
    one read of the active compiled bundle. Existing generation-valid packages
    are skipped. Registry defaults remain immutable and need no rows.
    """

    by_ref = {resource_ref(resource): resource for resource in resources}
    policy_generation = _policy_generation()
    pending: dict[str, int] = {}
    for ref in by_ref:
        generation = max(
            _shared_generation("resource", ref, _resource_generation.get(ref, 0)),
            policy_generation,
        )
        cached = _policy_package_cache.get(ref)
        if cached is None or cached[0] != generation:
            pending[ref] = generation
    if not pending:
        return 0

    _version, document = active_policy_bundle()
    table_rows = None
    if document is None:
        table_rows = list(
            AuthorizationPolicyOverride.objects.filter(resource_ref__in=pending).values_list(
                "resource_ref", "access_type", "policy"
            )
        )
    for ref, generation in pending.items():
        if document is None:
            source = (
                (operation, data) for row_ref, operation, data in table_rows if row_ref == ref
            )
        else:
            source = (document.get(ref) or {}).items()
        overrides = {
            str(operation).lower(): policy_from_data(data) for operation, data in source if data
        }
        _bounded_put(_policy_package_cache, ref, (generation, overrides))
    return len(pending)


def _authority_suppressed(principal) -> bool:
    """Read quell from the live attribute handler without I/O.

    The raw ``db_attrs`` column lags the handler's in-memory document (a quell
    set moments ago may not be flushed), so the handler is the only source that
    matches the inline path's immediacy. Falls back to the stored document when
    the handler cannot be built.
    """

    source = _authority_source(principal)
    try:
        return bool(source.attributes.get("_quell"))
    except AttributeError:
        return False
    except Exception:  # noqa: BLE001 - fall back to the last flushed document
        return _read_quell_document(source)


def _suppression_spec(principal) -> dict:
    """Describe how quell is resolved, without crossing into the worker.

    The owner thread reads the live handler (matching the inline path exactly,
    including unflushed quells). Only when the handler is unavailable does the
    worker read the stored ``db_attrs`` column itself -- plain ORM, no
    typeclass state -- which reproduces the ``_read_quell_document`` fallback.
    """

    source = _authority_source(principal)
    try:
        return {
            "known": True,
            "suppressed": bool(source.attributes.get("_quell")),
            "label": "",
            "pk": None,
        }
    except Exception:  # noqa: BLE001 - handler unavailable; resolve from the row
        pass
    meta = getattr(type(source), "_meta", None)
    pk = getattr(source, "pk", None)
    if meta is None or pk is None:
        # Non-model principals (sessions, test fakes) carry no stored document.
        return {"known": True, "suppressed": False, "label": "", "pk": None}
    return {
        "known": False,
        "suppressed": False,
        "label": meta.label_lower,
        "pk": int(pk),
    }


def _worker_suppression_map(specs) -> dict:
    """Worker: resolve unresolved quell flags from stored attribute documents."""

    from django.apps import apps

    pending: dict[str, list[tuple[object, int]]] = {}
    for spec in specs:
        suppression = spec.get("suppression")
        if not suppression or suppression["known"] or suppression["pk"] is None:
            continue
        pending.setdefault(suppression["label"], []).append((spec["cache_key"], suppression["pk"]))
    resolved: dict[object, bool] = {}
    for label, items in pending.items():
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        rows = dict(
            model._base_manager.filter(pk__in=[pk for _, pk in items]).values_list("pk", "db_attrs")
        )
        for cache_key, pk in items:
            resolved[cache_key] = _document_quell(rows.get(pk))
    return resolved


def _generation_is_fresh(namespace: str, ref: str, now: float) -> tuple[bool, int]:
    """Return local generation freshness without performing shared-cache I/O."""

    local_map = _principal_generation if namespace == "principal" else _resource_generation
    local = int(local_map.get(ref, 0))
    if not cached_setting("AUTHORIZATION_SHARED_INVALIDATION", True):
        return True, local
    cached = _shared_generation_cache.get(_generation_cache_key(namespace, ref))
    if invalidation.push_enabled():
        # Push invalidation keeps this entry authoritative until an event
        # drops the snapshot, so readiness no longer expires with a poll TTL.
        return True, max(local, int(cached[1]) if cached is not None else 0)
    interval = max(
        0.1,
        float(cached_setting("AUTHORIZATION_GENERATION_POLL_SECONDS", 2.0)),
    )
    if cached is None or now - cached[0] >= interval:
        return False, local
    return True, max(local, int(cached[1]))


def apply_invalidation_event(namespace: str, ref: str, generation: int) -> None:
    """Apply one pushed invalidation on the owner thread.

    Generations are applied monotonically and affected snapshots are dropped;
    the next read refills them. Unknown namespaces are ignored so a newer
    producer can add fact kinds without breaking older consumers.
    """

    global _policy_bundle_cache
    generation = int(generation)
    cache_key = _generation_cache_key(namespace, ref)
    if namespace == "principal":
        _principal_generation[ref] = max(_principal_generation.get(ref, 0), generation)
        _principal_cache.clear()
        _suspension_cache.clear()
    elif namespace == "resource":
        _resource_generation[ref] = max(_resource_generation.get(ref, 0), generation)
        _resource_cache.pop(ref, None)
        _policy_package_cache.pop(ref, None)
    elif namespace == "policy":
        # One activation invalidates every compiled policy package at once.
        _policy_bundle_cache = None
        _policy_package_cache.clear()
    else:
        return
    previous = _shared_generation_cache.get(cache_key)
    value = generation if previous is None else max(generation, int(previous[1]))
    _shared_generation_cache[cache_key] = (time.monotonic(), value)


def _prewarm_request(principals, resources, *, include_labels: bool) -> tuple[bool, dict]:
    """Build an IO-free snapshot request from owner-thread game objects."""

    now_mono = time.monotonic()
    now_ts = timezone.now().timestamp()
    principal_specs = []
    seen = set()
    ready = True
    for principal in principals:
        if principal is None or id(principal) in seen:
            continue
        seen.add(id(principal))
        refs = principal_refs(principal)
        cache_key = "|".join(refs) or "anonymous"
        generations = []
        generations_fresh = True
        for ref in refs:
            fresh, value = _generation_is_fresh("principal", ref, now_mono)
            generations_fresh = generations_fresh and fresh
            generations.append(value)
        grants = _principal_cache.get(cache_key)
        if grants is not None:
            # Group subjects participate in the generation check so a grant
            # edited on a group invalidates its members' cached snapshots.
            for ref in grants.group_refs:
                fresh, value = _generation_is_fresh("principal", ref, now_mono)
                generations_fresh = generations_fresh and fresh
                generations.append(value)
        generation = max(generations, default=0)
        suspension = _suspension_cache.get(cache_key)
        grants_valid = bool(
            grants is not None
            and grants.generation == generation
            and (grants.valid_until is None or grants.valid_until > now_ts)
        )
        suspension_valid = bool(
            suspension is not None
            and suspension[0] == generation
            and (suspension[2] is None or suspension[2] > now_ts)
        )
        ready = ready and generations_fresh and grants_valid and suspension_valid
        principal_specs.append(
            {
                "cache_key": cache_key,
                "refs": refs,
                "local_generations": {ref: int(_principal_generation.get(ref, 0)) for ref in refs},
                "cached_grants_generation": (grants.generation if grants is not None else None),
                "cached_grants_valid_until": (grants.valid_until if grants is not None else None),
                "cached_suspension_generation": (suspension[0] if suspension is not None else None),
                "cached_suspension_valid_until": (
                    suspension[2] if suspension is not None else None
                ),
                "suppression": _suppression_spec(principal) if include_labels else None,
            }
        )

    resource_specs = []
    seen.clear()
    for resource in resources:
        if resource is None or id(resource) in seen:
            continue
        seen.add(id(resource))
        try:
            ref = resource_ref(resource)
        except Exception:
            continue
        fresh, generation = _generation_is_fresh("resource", ref, now_mono)
        snapshot = _resource_cache.get(ref)
        policies = _policy_package_cache.get(ref)
        resource_valid = snapshot is not None and snapshot.generation == generation
        policy_generation = max(generation, _policy_generation())
        policy_valid = policies is not None and policies[0] == policy_generation
        ready = ready and fresh and resource_valid and policy_valid
        resource_specs.append(
            {
                "ref": ref,
                "local_generation": int(_resource_generation.get(ref, 0)),
                "cached_resource_generation": (
                    snapshot.generation if snapshot is not None else None
                ),
                "cached_policy_generation": (policies[0] if policies is not None else None),
                "base_labels": (
                    tuple(sorted(_computed_resource_labels(resource, ref)))
                    if include_labels
                    else ()
                ),
            }
        )
    return ready, {
        "principals": principal_specs,
        "resources": resource_specs,
        "shared_invalidation": bool(cached_setting("AUTHORIZATION_SHARED_INVALIDATION", True)),
        "now_ts": now_ts,
    }


def _fetch_authorization_snapshots(request: dict) -> dict:
    """Worker: refresh generations and load plain authorization rows in batches."""

    now = timezone.now()
    now_ts = now.timestamp()
    generation_values = {}
    generation_updates = []
    targets = []
    for spec in request["principals"]:
        targets.extend(
            ("principal", ref, local) for ref, local in spec["local_generations"].items()
        )
    targets.extend(
        ("resource", spec["ref"], spec["local_generation"]) for spec in request["resources"]
    )
    targets.append(("policy", "*", _policy_bundle_generation))
    shared = {}
    if request["shared_invalidation"] and targets:
        keys = [_generation_cache_key(namespace, ref) for namespace, ref, _ in targets]
        try:
            shared = cache.get_many(keys)
        except Exception:
            shared = {}
    for namespace, ref, local in targets:
        cache_key = _generation_cache_key(namespace, ref)
        value = max(int(local), int(shared.get(cache_key, 0) or 0))
        generation_values[(namespace, ref)] = value
        generation_updates.append((cache_key, value))

    principal_results = []
    stale_grant_refs = set()
    stale_suspension_refs = set()
    principal_state = []
    for spec in request["principals"]:
        generation = max(
            (generation_values.get(("principal", ref), 0) for ref in spec["refs"]),
            default=0,
        )
        grants_stale = spec["cached_grants_generation"] != generation or (
            spec["cached_grants_valid_until"] is not None
            and spec["cached_grants_valid_until"] <= now_ts
        )
        suspension_stale = spec["cached_suspension_generation"] != generation or (
            spec["cached_suspension_valid_until"] is not None
            and spec["cached_suspension_valid_until"] <= now_ts
        )
        if grants_stale:
            stale_grant_refs.update(spec["refs"])
        if suspension_stale:
            stale_suspension_refs.update(spec["refs"])
        principal_state.append((spec, generation, grants_stale, suspension_stale))

    group_map: dict[str, set[str]] = {}
    group_refs_all: set[str] = set()
    if stale_grant_refs:
        for group_ref, principal_ref in AuthorizationGroupMembership.objects.filter(
            principal_ref__in=stale_grant_refs
        ).values_list("group_ref", "principal_ref"):
            group_map.setdefault(principal_ref, set()).add(f"group:{group_ref}")
        group_refs_all = {ref for refs in group_map.values() for ref in refs}
    if group_refs_all:
        group_keys = [_generation_cache_key("principal", ref) for ref in group_refs_all]
        try:
            group_shared = cache.get_many(group_keys)
        except Exception:
            group_shared = {}
        for ref in group_refs_all:
            key = _generation_cache_key("principal", ref)
            value = max(
                int(_principal_generation.get(ref, 0)),
                int(group_shared.get(key, 0) or 0),
            )
            generation_values[("principal", ref)] = value
            generation_updates.append((key, value))

    grant_rows = []
    if stale_grant_refs or group_refs_all:
        rows = (
            AuthorizationGrant.objects.filter(
                principal_ref__in=stale_grant_refs | group_refs_all,
                revoked_at__isnull=True,
            )
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .values(
                "grant_id",
                "principal_ref",
                "capability",
                "scope_kind",
                "scope_key",
                "constraints",
                "expires_at",
            )
        )
        grant_rows = [
            {
                **row,
                "expires_at": (row["expires_at"].timestamp() if row["expires_at"] else None),
            }
            for row in rows
        ]

    suspension_rows = []
    if stale_suspension_refs:
        rows = (
            AuthorizationPrincipalState.objects.filter(
                principal_ref__in=stale_suspension_refs,
                suspended=True,
            )
            .filter(models.Q(suspended_until__isnull=True) | models.Q(suspended_until__gt=now))
            .values("principal_ref", "suspended_until")
        )
        suspension_rows = [
            (
                row["principal_ref"],
                row["suspended_until"].timestamp() if row["suspended_until"] else None,
            )
            for row in rows
        ]

    resolved_suppression = _worker_suppression_map(request["principals"])
    for spec, generation, grants_stale, suspension_stale in principal_state:
        spec_group_refs = (
            set().union(*(group_map.get(ref, set()) for ref in spec["refs"]))
            if grants_stale and group_map
            else set()
        )
        if spec_group_refs:
            generation = max(
                [
                    generation,
                    *(generation_values.get(("principal", ref), 0) for ref in spec_group_refs),
                ]
            )
        refs = set(spec["refs"]) | spec_group_refs
        result = {
            "cache_key": spec["cache_key"],
            "generation": generation,
            "grants_stale": grants_stale,
            "suspension_stale": suspension_stale,
        }
        if grants_stale:
            suppression = spec.get("suppression") or {}
            result["group_refs"] = tuple(sorted(spec_group_refs))
            result["grants"] = [row for row in grant_rows if row["principal_ref"] in refs]
            result["suppressed"] = (
                bool(suppression.get("suppressed"))
                if suppression.get("known")
                else bool(resolved_suppression.get(spec["cache_key"]))
            )
        if suspension_stale:
            expiries = [expiry for ref, expiry in suspension_rows if ref in refs]
            result["suspended"] = bool(expiries)
            result["suspended_until"] = None if None in expiries else min(expiries, default=None)
        principal_results.append(result)

    resource_state = []
    stale_resource_refs = set()
    stale_policy_refs = set()
    policy_generation = int(generation_values.get(("policy", "*"), 0))
    for spec in request["resources"]:
        generation = generation_values.get(("resource", spec["ref"]), 0)
        resource_stale = spec["cached_resource_generation"] != generation
        package_generation = max(generation, policy_generation)
        policy_stale = spec["cached_policy_generation"] != package_generation
        if resource_stale:
            stale_resource_refs.add(spec["ref"])
        if policy_stale:
            stale_policy_refs.add(spec["ref"])
        resource_state.append((spec, generation, package_generation, resource_stale, policy_stale))

    label_rows = []
    if stale_resource_refs:
        label_rows = list(
            AuthorizationScopeLabel.objects.filter(
                resource_ref__in=stale_resource_refs
            ).values_list("resource_ref", "label")
        )
    policy_rows = []
    if stale_policy_refs:
        bundle_row = (
            AuthorizationPolicyBundle.objects.filter(active=True)
            .order_by("-version")
            .values_list("document", flat=True)
            .first()
        )
        if bundle_row is None:
            # Bundles are opt-in; without one, live override rows stay authoritative.
            policy_rows = list(
                AuthorizationPolicyOverride.objects.filter(
                    resource_ref__in=stale_policy_refs
                ).values_list("resource_ref", "access_type", "policy")
            )
        else:
            bundle_document = bundle_row or {}
            for ref in stale_policy_refs:
                for operation, data in (bundle_document.get(ref) or {}).items():
                    if data:
                        policy_rows.append((ref, operation, data))
    resource_results = []
    for spec, generation, package_generation, resource_stale, policy_stale in resource_state:
        result = {
            "ref": spec["ref"],
            "generation": generation,
            "policy_generation": package_generation,
            "resource_stale": resource_stale,
            "policy_stale": policy_stale,
        }
        if resource_stale:
            result["labels"] = [label for ref, label in label_rows if ref == spec["ref"]]
            result["base_labels"] = spec["base_labels"]
        if policy_stale:
            result["policies"] = [
                (operation, data) for ref, operation, data in policy_rows if ref == spec["ref"]
            ]
        resource_results.append(result)
    return {
        "generation_updates": generation_updates,
        "principals": principal_results,
        "resources": resource_results,
    }


def _install_authorization_snapshots(result: dict) -> None:
    """Owner thread: adopt worker primitives into the bounded local caches."""

    refreshed_at = time.monotonic()
    for cache_key, generation in result["generation_updates"]:
        # Concurrent prewarms can install out of order; never let an older
        # in-flight batch lower the generation a newer batch already observed.
        previous = _shared_generation_cache.get(cache_key)
        value = int(generation)
        if previous is not None:
            value = max(value, int(previous[1]))
        _shared_generation_cache[cache_key] = (refreshed_at, value)
    for item in result["principals"]:
        cache_key = item["cache_key"]
        generation = int(item["generation"])
        if item["grants_stale"]:
            by_capability: dict[str, set[GrantScope]] = {}
            valid_until = None
            if not item.get("suppressed"):
                for grant in item.get("grants", ()):
                    constraints = dict(grant.get("constraints") or {})
                    if set(constraints) - grant_constraint_keys() or any(
                        value is not None and not isinstance(value, (str, int, float, bool))
                        for value in constraints.values()
                    ):
                        logger.log_err(
                            f"Ignoring malformed authorization grant {grant.get('grant_id')}: "
                            "invalid constraints"
                        )
                        continue
                    by_capability.setdefault(grant["capability"], set()).add(
                        GrantScope(
                            grant["principal_ref"],
                            grant["scope_kind"],
                            grant["scope_key"],
                            tuple(sorted(constraints.items())),
                        )
                    )
                    expiry = grant.get("expires_at")
                    if expiry is not None:
                        valid_until = expiry if valid_until is None else min(valid_until, expiry)
            _bounded_put(
                _principal_cache,
                cache_key,
                GrantSnapshot(
                    cache_key,
                    {key: frozenset(values) for key, values in by_capability.items()},
                    generation,
                    valid_until,
                    frozenset(item.get("group_refs", ())),
                ),
            )
        if item["suspension_stale"]:
            _bounded_put(
                _suspension_cache,
                cache_key,
                (generation, bool(item.get("suspended")), item.get("suspended_until")),
            )
    for item in result["resources"]:
        ref = item["ref"]
        generation = int(item["generation"])
        if item["resource_stale"]:
            labels = set(item.get("base_labels", ()))
            labels.update(str(label).strip().lower() for label in item.get("labels", ()))
            _bounded_put(
                _resource_cache,
                ref,
                ResourceSnapshot(ref, ref.split(":", 1)[0], frozenset(labels), generation),
            )
        if item["policy_stale"]:
            overrides = {
                str(operation).lower(): policy_from_data(data)
                for operation, data in item.get("policies", ())
                if data
            }
            _bounded_put(
                _policy_package_cache,
                ref,
                (int(item.get("policy_generation", generation)), overrides),
            )


async def _execute_authorization_prewarm(request: dict) -> bool:
    """Run one primitive snapshot request and install its result."""

    started = time.perf_counter()
    try:
        from evennia.utils import clock, defer

        if clock.loop_running():
            result = await clock.maybe_await(
                defer.in_thread(_fetch_authorization_snapshots, request)
            )
        else:
            result = _fetch_authorization_snapshots(request)
        _install_authorization_snapshots(result)
        outcome = "refreshed"
        return True
    except Exception:
        outcome = "failed"
        logger.log_trace("authorization off-loop prewarm failed")
        return False
    finally:
        try:
            from evennia.server.prometheus_metrics import record_authorization_prewarm

            record_authorization_prewarm(outcome, time.perf_counter() - started)
        except Exception:
            pass


async def _flush_authorization_prewarm() -> bool:
    """Coalesce callers from one event-loop turn into one worker batch."""

    global _prewarm_task
    await asyncio.sleep(0)
    principals = tuple(_pending_prewarm_principals.values())
    resources = tuple(_pending_prewarm_resources.values())
    _pending_prewarm_principals.clear()
    _pending_prewarm_resources.clear()
    _, request = _prewarm_request(principals, resources, include_labels=True)
    try:
        return await _execute_authorization_prewarm(request)
    finally:
        if _prewarm_task is asyncio.current_task():
            _prewarm_task = None


def _finish_prewarm_wait(value: bool, outcome: str, started: float) -> bool:
    """Record one action's critical-path snapshot wait and return its result."""

    try:
        from evennia.server.prometheus_metrics import record_authorization_prewarm_wait

        record_authorization_prewarm_wait(outcome, time.perf_counter() - started)
    except Exception:
        pass
    return value


async def prewarm_authorization(principals, resources) -> bool:
    """Batch-refresh authorization state off the IO owner before rule evaluation."""

    global _prewarm_loop, _prewarm_task
    if not getattr(settings, "AUTHORIZATION_OFFLOOP_SNAPSHOTS", False):
        return True
    started = time.perf_counter()
    principals = tuple(principals)
    resources = tuple(resources)
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    if current_task is None:
        ready, _ = _prewarm_request(principals, resources, include_labels=False)
        if ready:
            return _finish_prewarm_wait(True, "ready", started)
        _, request = _prewarm_request(principals, resources, include_labels=True)
        refreshed = await _execute_authorization_prewarm(request)
        return _finish_prewarm_wait(refreshed, "waited" if refreshed else "failed", started)
    loop = current_task.get_loop()
    if _prewarm_loop is not loop:
        _prewarm_loop = loop
        _prewarm_task = None
        _pending_prewarm_principals.clear()
        _pending_prewarm_resources.clear()
    waited = False
    for _attempt in range(3):
        ready, _ = _prewarm_request(principals, resources, include_labels=False)
        if ready:
            return _finish_prewarm_wait(True, "waited" if waited else "ready", started)
        for principal in principals:
            if principal is not None:
                _pending_prewarm_principals[id(principal)] = principal
        for resource in resources:
            if resource is not None:
                _pending_prewarm_resources[id(resource)] = resource
        if _prewarm_task is None or _prewarm_task.done():
            _prewarm_task = loop.create_task(_flush_authorization_prewarm())
        try:
            waited = True
            if not await asyncio.shield(_prewarm_task):
                return _finish_prewarm_wait(False, "failed", started)
        except asyncio.CancelledError:
            # A reload or test clearing the shared prewarm task must not cancel
            # the action; only a cancellation of this task itself may propagate.
            if current_task.cancelling():
                raise
            return _finish_prewarm_wait(False, "failed", started)
    ready, _ = _prewarm_request(principals, resources, include_labels=False)
    return _finish_prewarm_wait(ready, "waited" if ready else "failed", started)


async def ensure_authorization(principals, resources) -> bool:
    """Return whether rule evaluation may use the local snapshot scope.

    With push invalidation, facts stay valid until an invalidation event drops
    them, so warm decisions return immediately and a genuinely cold fact costs
    at most one coalesced off-loop fetch. Without push invalidation this
    preserves the bounded prewarm behavior.
    """

    if not getattr(settings, "AUTHORIZATION_OFFLOOP_SNAPSHOTS", False):
        return True
    if not invalidation.push_enabled():
        return await prewarm_authorization(principals, resources)
    invalidation.start()
    principals = tuple(principals)
    resources = tuple(resources)
    ready, _ = _prewarm_request(principals, resources, include_labels=False)
    if ready:
        return True

    global _prewarm_loop, _prewarm_task
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    if current_task is None:
        _, request = _prewarm_request(principals, resources, include_labels=True)
        return await _execute_authorization_prewarm(request)
    loop = current_task.get_loop()
    if _prewarm_loop is not loop:
        _prewarm_loop = loop
        _prewarm_task = None
        _pending_prewarm_principals.clear()
        _pending_prewarm_resources.clear()
    for _attempt in range(2):
        for principal in principals:
            if principal is not None:
                _pending_prewarm_principals[id(principal)] = principal
        for resource in resources:
            if resource is not None:
                _pending_prewarm_resources[id(resource)] = resource
        if _prewarm_task is None or _prewarm_task.done():
            _prewarm_task = loop.create_task(_flush_authorization_prewarm())
        try:
            if not await asyncio.shield(_prewarm_task):
                return False
        except asyncio.CancelledError:
            # A reload or test clearing the shared task must not cancel the
            # action; only a cancellation of this task itself may propagate.
            if current_task.cancelling():
                raise
            return False
        ready, _ = _prewarm_request(principals, resources, include_labels=False)
        if ready:
            return True
    return False


def clear_authorization_caches() -> None:
    """Clear runtime caches for reloads and deterministic tests."""

    global _prewarm_task, _policy_bundle_cache, _policy_bundle_generation
    _principal_cache.clear()
    _resource_cache.clear()
    _policy_package_cache.clear()
    _suspension_cache.clear()
    _shared_generation_cache.clear()
    _principal_generation.clear()
    _resource_generation.clear()
    _policy_bundle_cache = None
    _policy_bundle_generation = 0
    _pending_prewarm_principals.clear()
    _pending_prewarm_resources.clear()
    _snapshot_miss_warned_at.clear()
    if _prewarm_task is not None and not _prewarm_task.done():
        _prewarm_task.cancel()
    _prewarm_task = None
