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
PROVENANCE = "migration:server.0013_split_engine_keys"


def split_keys(apps, schema_editor):
    """Grant each split-out key wherever its broad key is held, scope for scope.

    A key that one source gives is a child of it, so revoking the source
    revokes the key. A key that two sources give is a grant of its own, so
    revoking one source does not take what the other still gives. It keeps the
    expiry of the source that lasts longest.
    """

    from django.db.models import F

    AuthorizationGrant = apps.get_model("server", "AuthorizationGrant")
    AuthorizationAuditEvent = apps.get_model("server", "AuthorizationAuditEvent")
    held = {
        (grant.principal_ref, grant.capability, grant.scope_kind, grant.scope_key)
        for grant in AuthorizationGrant.objects.filter(revoked_at__isnull=True)
    }
    planned = {}
    sources = AuthorizationGrant.objects.filter(
        capability__in=SPLIT_KEYS, revoked_at__isnull=True
    ).order_by(F("expires_at").desc(nulls_first=True), "id")
    for source in sources:
        for capability in SPLIT_KEYS[source.capability]:
            if source.parent_grant_id and capability not in DELEGABLE:
                continue
            slot = (source.principal_ref, capability, source.scope_kind, source.scope_key)
            if slot not in held:
                planned.setdefault(slot, []).append(source)
    grants, events = [], []
    for (principal_ref, capability, scope_kind, scope_key), slot_sources in planned.items():
        longest = slot_sources[0]
        grants.append(
            AuthorizationGrant(
                grant_id=uuid.uuid4().hex,
                principal_ref=principal_ref,
                capability=capability,
                scope_kind=scope_kind,
                scope_key=scope_key,
                constraints=longest.constraints,
                expires_at=longest.expires_at,
                provenance=PROVENANCE,
                parent_grant_id=longest.grant_id if len(slot_sources) == 1 else "",
            )
        )
        split_from = ", ".join(
            f"{source.capability} (grant {source.grant_id})" for source in slot_sources
        )
        events.append(
            AuthorizationAuditEvent(
                event_id=uuid.uuid4().hex,
                kind="grant_created",
                principal_ref=principal_ref,
                capability=capability,
                resource_ref=scope_key if scope_kind == "resource" else "",
                actor_ref="system:migration",
                reason=f"Split from {split_from}."[:255],
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
        frontier = set(
            AuthorizationGrant.objects.filter(parent_grant_id__in=frontier).values_list(
                "grant_id", flat=True
            )
        ) - removed
    AuthorizationGrant.objects.filter(grant_id__in=removed).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("server", "0012_authorization_groups_and_policy_bundle"),
    ]

    operations = [
        migrations.RunPython(split_keys, unsplit_keys),
    ]
