"""Give holders of the two broad engine keys the narrower keys split out of them.

``engine.world.build`` used to gate ``@force``, ``@emit``, ``@policy``, ``@scope``
and database search; ``engine.moderation.manage`` gated ``@wall`` and
``@userpassword``. Those verbs now check their own keys, so every principal that
held a broad key keeps each verb it could run.
"""

import uuid

from django.db import migrations

SPLIT_KEYS = {
    "engine.world.build": (
        "engine.object.force",
        "engine.authorization.manage",
        "engine.world.announce",
        "engine.world.search",
    ),
    "engine.moderation.manage": (
        "engine.world.announce",
        "engine.account.password",
    ),
}
# A delegated grant passes on only what could have been delegated to begin with.
DELEGABLE = {"engine.world.search"}
PROVENANCE = "migration:server.0014_split_engine_keys"


def split_keys(apps, schema_editor):
    """Grant each split-out key as a child of each broad grant, on that grant's terms.

    An expired source is split too, so renewing it brings its keys back. A key
    that two sources give is two rows, and each goes when its own source goes.
    """

    AuthorizationGrant = apps.get_model("server", "AuthorizationGrant")
    AuthorizationAuditEvent = apps.get_model("server", "AuthorizationAuditEvent")
    sources = list(
        AuthorizationGrant.objects.filter(capability__in=SPLIT_KEYS, revoked_at__isnull=True)
    )
    held = set(
        AuthorizationGrant.objects.filter(
            parent_grant_id__in=[source.grant_id for source in sources],
            revoked_at__isnull=True,
        ).values_list("parent_grant_id", "capability", "scope_kind", "scope_key")
    )
    grants, events = [], []
    for source in sources:
        for capability in SPLIT_KEYS[source.capability]:
            if source.parent_grant_id and capability not in DELEGABLE:
                continue
            if (source.grant_id, capability, source.scope_kind, source.scope_key) in held:
                continue
            grants.append(
                AuthorizationGrant(
                    grant_id=uuid.uuid4().hex,
                    principal_ref=source.principal_ref,
                    capability=capability,
                    scope_kind=source.scope_kind,
                    scope_key=source.scope_key,
                    constraints=source.constraints,
                    expires_at=source.expires_at,
                    provenance=PROVENANCE,
                    parent_grant_id=source.grant_id,
                )
            )
            events.append(
                AuthorizationAuditEvent(
                    event_id=uuid.uuid4().hex,
                    kind="grant_created",
                    principal_ref=source.principal_ref,
                    capability=capability,
                    resource_ref=source.scope_key if source.scope_kind == "resource" else "",
                    actor_ref="system:migration",
                    reason=f"Split from {source.capability} (grant {source.grant_id}).",
                )
            )
    AuthorizationGrant.objects.bulk_create(grants)
    AuthorizationAuditEvent.objects.bulk_create(events)


def unsplit_keys(apps, schema_editor):
    """Remove the grants this migration made and every grant delegated from them."""

    AuthorizationGrant = apps.get_model("server", "AuthorizationGrant")
    removed = set()
    frontier = set(
        AuthorizationGrant.objects.filter(provenance=PROVENANCE).values_list("grant_id", flat=True)
    )
    while frontier:
        removed |= frontier
        frontier = (
            set(
                AuthorizationGrant.objects.filter(parent_grant_id__in=frontier).values_list(
                    "grant_id", flat=True
                )
            )
            - removed
        )
    AuthorizationGrant.objects.filter(grant_id__in=removed).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("server", "0013_grant_source_uniq"),
    ]

    operations = [
        migrations.RunPython(split_keys, unsplit_keys),
    ]
