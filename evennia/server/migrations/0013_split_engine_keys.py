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

    Each new grant is a child of its source, so revoking the source revokes it.
    Where two sources give the same key, the one that lasts longest wins.
    """

    from django.db.models import F

    AuthorizationGrant = apps.get_model("server", "AuthorizationGrant")
    AuthorizationAuditEvent = apps.get_model("server", "AuthorizationAuditEvent")
    held = {
        (grant.principal_ref, grant.capability, grant.scope_kind, grant.scope_key)
        for grant in AuthorizationGrant.objects.filter(revoked_at__isnull=True)
    }
    grants, events = [], []
    sources = AuthorizationGrant.objects.filter(
        capability__in=SPLIT_KEYS, revoked_at__isnull=True
    ).order_by(F("expires_at").desc(nulls_first=True), "id")
    for source in sources:
        for capability in SPLIT_KEYS[source.capability]:
            if source.parent_grant_id and capability not in DELEGABLE:
                continue
            slot = (source.principal_ref, capability, source.scope_kind, source.scope_key)
            if slot in held:
                continue
            held.add(slot)
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
    """Remove only the grants this migration made."""

    apps.get_model("server", "AuthorizationGrant").objects.filter(provenance=PROVENANCE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("server", "0012_authorization_groups_and_policy_bundle"),
    ]

    operations = [
        migrations.RunPython(split_keys, unsplit_keys),
    ]
