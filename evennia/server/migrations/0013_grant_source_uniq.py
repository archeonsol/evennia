"""Let a principal hold one active grant per scope and capability for each source."""

from django.db import migrations, models


def collapse_duplicate_slots(apps, schema_editor):
    """Revoke all but one active grant per slot so the narrower constraint holds.

    The direct grant is kept if there is one, otherwise the newest.
    """

    from django.utils import timezone

    AuthorizationGrant = apps.get_model("server", "AuthorizationGrant")
    kept = set()
    surplus = []
    for grant in AuthorizationGrant.objects.filter(revoked_at__isnull=True).order_by(
        "parent_grant_id", "-created_at", "-id"
    ):
        slot = (grant.principal_ref, grant.capability, grant.scope_kind, grant.scope_key)
        if slot in kept:
            surplus.append(grant.id)
        else:
            kept.add(slot)
    AuthorizationGrant.objects.filter(id__in=surplus).update(revoked_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ("server", "0012_authorization_groups_and_policy_bundle"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="authorizationgrant",
            name="authgrant_active_scope_uniq",
        ),
        migrations.AddConstraint(
            model_name="authorizationgrant",
            constraint=models.UniqueConstraint(
                condition=models.Q(("revoked_at__isnull", True)),
                fields=(
                    "principal_ref",
                    "capability",
                    "scope_kind",
                    "scope_key",
                    "parent_grant_id",
                ),
                name="authgrant_active_source_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="authorizationgrant",
            index=models.Index(fields=["parent_grant_id"], name="authgrant_parent_idx"),
        ),
        migrations.RunPython(migrations.RunPython.noop, collapse_duplicate_slots),
    ]
