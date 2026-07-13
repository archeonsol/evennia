"""Offline persistence for one-way lockstring imports."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from evennia.server.models import AuthorizationPolicyOverride

from ..policy import policy_from_data
from ..storage import bump_resource_generation, grant_capability, resource_ref
from .compiler import CompilationError, compile_lockstring


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Result of importing one resource while the game is offline."""

    resource_ref: str
    policies_written: int
    grants_written: int
    frozen: bool
    source: str
    warnings: tuple[str, ...] = ()


def migrate_resource(resource, *, source=None, freeze: bool = False) -> MigrationResult:
    """Compile one historical row into typed storage without a live fallback."""

    ref = resource_ref(resource)
    lock_source = str(source if source is not None else getattr(resource, "lock_storage", ""))
    compiled = compile_lockstring(lock_source, resource_ref=ref)
    encoded = repr({key: policy.to_data() for key, policy in compiled.policies.items()})
    if "legacy." in encoded:
        raise CompilationError("legacy predicate requires explicit capability policy authoring")
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
                    "template_key": "legacy.imported",
                    "policy": policy.to_data(),
                    "legacy_shadow": "",
                    "legacy_frozen": False,
                },
            )
        for grant in compiled.grants:
            grant_capability(
                grant.principal_ref,
                grant.capability,
                scope_kind=grant.scope_kind,
                scope_key=grant.scope_key,
                provenance="offline_lock_import",
                reason="one-way R3F import",
            )
        transaction.on_commit(lambda: bump_resource_generation(resource))
    return MigrationResult(
        ref,
        len(compiled.policies),
        len(compiled.grants),
        False,
        lock_source,
        compiled.warnings,
    )


def migrate_resources(resources):
    """Yield a result or explicit compilation error per resource."""

    for resource in resources:
        try:
            yield migrate_resource(resource)
        except CompilationError as err:
            yield err
