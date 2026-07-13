"""Fail deployment when executable legacy authority remains."""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Audit the persisted capability-only cutover boundary."""

    help = "Verify that no live resource retains executable lock storage."

    def handle(self, *args, **options):
        """Inspect every resource table using bounded aggregate queries."""

        from evennia.accounts.models import AccountDB
        from evennia.comms.models import ChannelDB, Msg
        from evennia.help.models import HelpEntry
        from evennia.objects.models import ObjectDB
        from evennia.scripts.models import ScriptDB
        from evennia.server.models import AuthorizationPolicyOverride

        failures = []
        for model in (AccountDB, ObjectDB, ScriptDB, ChannelDB, Msg, HelpEntry):
            count = model.objects.exclude(db_lock_storage="").count()
            if count:
                failures.append(f"{model.__name__}: legacy lock rows={count}")
        shadow = AuthorizationPolicyOverride.objects.exclude(legacy_shadow="").count()
        if shadow:
            failures.append(f"AuthorizationPolicyOverride: legacy shadows={shadow}")
        if failures:
            raise CommandError("R3F authorization audit failed: " + "; ".join(failures))
        self.stdout.write(self.style.SUCCESS("R3F authorization audit passed"))
