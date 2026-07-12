"""Issue a short-lived deployment-console recovery grant."""

from django.core.management.base import BaseCommand, CommandError

from evennia.authorization.storage import issue_recovery_grant


class Command(BaseCommand):
    """Issue an audited break-glass grant without an immortal game account."""

    help = "Issue a temporary engine.authorization.break_glass grant."

    def add_arguments(self, parser):
        """Declare account, reason, and bounded TTL arguments."""

        parser.add_argument("account_id", type=int)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--ttl", type=int, default=900)

    def handle(self, *args, **options):
        """Create the recovery grant and report its expiry."""

        reason = str(options["reason"]).strip()
        if not reason:
            raise CommandError("--reason cannot be empty")
        grant = issue_recovery_grant(
            options["account_id"],
            reason=reason,
            ttl_seconds=options["ttl"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Issued {grant.capability} to {grant.principal_ref} until {grant.expires_at}."
            )
        )
