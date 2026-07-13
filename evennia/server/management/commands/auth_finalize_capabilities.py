"""Materialize legacy permission authority into explicit R3F grants."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evennia.accounts.models import AccountDB
from evennia.authorization.capabilities import capability_registry
from evennia.authorization.storage import grant_capability
from evennia.objects.models import ObjectDB


def _normalized_mapping() -> dict[str, tuple[str, ...]]:
    """Return the validated configured permission-to-grant import map."""

    mapping = {}
    for permission, tokens in dict(
        getattr(settings, "AUTHORIZATION_PERMISSION_MIGRATION", {})
    ).items():
        key = str(permission).strip().lower().rstrip("s")
        if not key:
            raise CommandError("authorization permission migration contains an empty key")
        capabilities = []
        for token in tokens:
            token = str(token).strip().lower()
            if token.startswith("bundle:"):
                capabilities.extend(capability_registry.expand_bundle(token.split(":", 1)[1]))
            else:
                capabilities.append(capability_registry.require(token).key)
        mapping[key] = tuple(sorted(set(capabilities)))
    return mapping


class Command(BaseCommand):
    """Convert permission tags to durable grants and optionally remove the tags."""

    help = "Materialize legacy permission authority as explicit capability grants."

    def add_arguments(self, parser):
        """Declare safe dry-run and destructive application switches."""

        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--remove-authority-tags", action="store_true")
        parser.add_argument("--clear-lock-storage", action="store_true")

    def handle(self, *args, **options):
        """Import every mapped account/object permission idempotently."""

        mapping = _normalized_mapping()
        apply = bool(options["apply"])
        remove = bool(options["remove_authority_tags"])
        clear_locks = bool(options["clear_lock_storage"])
        if (remove or clear_locks) and not apply:
            raise CommandError("destructive finalization switches require --apply")
        rows = []
        for prefix, queryset in (
            ("account", AccountDB.objects.all().iterator(chunk_size=500)),
            ("object", ObjectDB.objects.all().iterator(chunk_size=500)),
        ):
            for principal in queryset:
                names = {
                    str(name).strip().lower().rstrip("s")
                    for name in principal.permissions.all()
                }
                selected = sorted(names & mapping.keys())
                if not selected:
                    continue
                capabilities = sorted(
                    {capability for name in selected for capability in mapping[name]}
                )
                rows.append((prefix, principal, selected, capabilities))

        grant_count = sum(len(row[3]) for row in rows)
        self.stdout.write(
            f"R3F capability import: principals={len(rows)} grants={grant_count} "
            f"mode={'apply' if apply else 'dry-run'}"
        )
        if not apply:
            return
        for prefix, principal, selected, capabilities in rows:
            principal_ref = f"{prefix}:{principal.pk}"
            for capability in capabilities:
                grant_capability(
                    principal_ref,
                    capability,
                    scope_kind="world",
                    scope_key="*",
                    provenance="r3f_permission_import",
                    actor_ref="deployment:migration",
                    reason="R3F permission authority retirement",
                )
            if remove:
                for permission in selected:
                    principal.permissions.remove(permission)
        if clear_locks:
            from evennia.comms.models import ChannelDB, Msg
            from evennia.help.models import HelpEntry
            from evennia.scripts.models import ScriptDB

            for model in (AccountDB, ObjectDB, ScriptDB, ChannelDB, Msg, HelpEntry):
                model.objects.exclude(db_lock_storage="").update(db_lock_storage="")
        self.stdout.write(self.style.SUCCESS("R3F capability import complete"))
