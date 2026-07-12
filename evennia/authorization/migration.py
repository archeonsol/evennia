"""R3D/R3E offline migration, differential replay, and input freezing."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError, transaction

from evennia.server.models import AuthorizationPolicyOverride

from .compiler import CompilationError, compile_lockstring
from .policy import policy_from_data
from .service import authorize
from .storage import (
    bump_resource_generation,
    grant_capability,
    resource_ref,
)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Result of compiling and storing one resource's legacy policies."""

    resource_ref: str
    policies_written: int
    grants_written: int
    frozen: bool
    source: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DifferentialResult:
    """One offline comparison between legacy and structured decisions."""

    resource_ref: str
    principal_ref: str
    access_type: str
    legacy_allowed: bool
    structured_allowed: bool
    reason_code: str

    @property
    def agrees(self) -> bool:
        """Return whether both evaluators produced the same result."""

        return self.legacy_allowed == self.structured_allowed


def migrate_resource(resource, *, source=None, freeze: bool = False) -> MigrationResult:
    """Compile one resource's lock storage into policies and grants atomically."""

    ref = resource_ref(resource)
    lock_source = str(source if source is not None else getattr(resource, "lock_storage", ""))
    compiled = compile_lockstring(lock_source, resource_ref=ref)
    for policy in compiled.policies.values():
        policy_from_data(policy.to_data())
    with transaction.atomic():
        access_types = set(compiled.policies)
        AuthorizationPolicyOverride.objects.filter(resource_ref=ref).exclude(
            access_type__in=access_types
        ).delete()
        for access_type, policy in compiled.policies.items():
            AuthorizationPolicyOverride.objects.update_or_create(
                resource_ref=ref,
                access_type=access_type,
                defaults={
                    "template_key": "legacy.compiled",
                    "policy": policy.to_data(),
                    "legacy_shadow": lock_source,
                    "legacy_frozen": bool(freeze),
                },
            )
        for grant in compiled.grants:
            grant_capability(
                grant.principal_ref,
                grant.capability,
                scope_kind=grant.scope_kind,
                scope_key=grant.scope_key,
                provenance=grant.provenance,
                reason="R3 legacy identity-lock migration",
            )
        transaction.on_commit(lambda: bump_resource_generation(resource))
    return MigrationResult(
        ref,
        len(compiled.policies),
        len(compiled.grants),
        bool(freeze),
        lock_source,
        compiled.warnings,
    )


def migrate_resources(resources, *, freeze: bool = False):
    """Yield bounded per-resource migration results or explicit errors."""

    for resource in resources:
        try:
            yield migrate_resource(resource, freeze=freeze)
        except CompilationError as err:
            yield err


def differential_replay(resources, principals, access_types) -> tuple[DifferentialResult, ...]:
    """Compare both evaluators over a frozen offline input matrix."""

    results = []
    for resource in resources:
        ref = resource_ref(resource)
        for principal in principals:
            principal_ref = str(getattr(principal, "pk", getattr(principal, "id", "unknown")))
            for access_type in access_types:
                legacy = bool(resource.locks.check(principal, access_type, default=False))
                decision = authorize(principal, resource, access_type, default=False)
                results.append(
                    DifferentialResult(
                        ref,
                        principal_ref,
                        str(access_type),
                        legacy,
                        decision.allowed,
                        decision.reason_code,
                    )
                )
    return tuple(results)


def _kind_is_frozen(resource) -> bool:
    """Return whether writes for a resource kind have crossed the freeze gate."""

    kind = resource_ref(resource).split(":", 1)[0]
    return kind in set(getattr(settings, "AUTHORIZATION_FROZEN_RESOURCE_KINDS", ()))


def frozen_source(resource) -> str | None:
    """Return the current frozen legacy representation, if any."""

    if not _kind_is_frozen(resource):
        return None
    try:
        row = (
            AuthorizationPolicyOverride.objects.filter(
                resource_ref=resource_ref(resource), legacy_frozen=True
            )
            .only("legacy_shadow")
            .first()
        )
    except DatabaseError:
        # Fresh-install migrations may create lock-bearing rows before the R3
        # server migration has created authorization tables. Preserve legacy
        # writes during that finite bootstrap window.
        if any(command in sys.argv for command in ("migrate", "test")):
            return None
        raise
    return row.legacy_shadow if row else str(getattr(resource, "lock_storage", ""))


def handle_frozen_add(resource, lockstring: str) -> str | None:
    """Compile a legacy add into structured storage without writing a lockstring."""

    current = frozen_source(resource)
    if current is None:
        return None
    combined = ";".join(part for part in (current, lockstring) if part)
    result = migrate_resource(resource, source=combined, freeze=True)
    return result.source


def handle_frozen_remove(resource, access_type: str) -> tuple[bool, str | None]:
    """Remove one access policy from frozen structured storage."""

    current = frozen_source(resource)
    if current is None:
        return False, None
    clauses = [
        clause
        for clause in current.split(";")
        if clause.strip() and clause.split(":", 1)[0].strip() != access_type
    ]
    existed = len(clauses) != len([part for part in current.split(";") if part.strip()])
    new_source = ";".join(clauses)
    ref = resource_ref(resource)
    if new_source:
        migrate_resource(resource, source=new_source, freeze=True)
    else:
        AuthorizationPolicyOverride.objects.filter(resource_ref=ref).delete()
        AuthorizationPolicyOverride.objects.create(
            resource_ref=ref,
            access_type="__frozen__",
            template_key="migration.empty",
            policy={},
            legacy_shadow="",
            legacy_frozen=True,
        )
        bump_resource_generation(resource)
    return existed, new_source


def handle_frozen_clear(resource) -> bool:
    """Clear structured policies for a frozen resource kind."""

    if frozen_source(resource) is None:
        return False
    ref = resource_ref(resource)
    AuthorizationPolicyOverride.objects.filter(resource_ref=ref).delete()
    AuthorizationPolicyOverride.objects.create(
        resource_ref=ref,
        access_type="__frozen__",
        template_key="migration.empty",
        policy={},
        legacy_shadow="",
        legacy_frozen=True,
    )
    bump_resource_generation(resource)
    return True
