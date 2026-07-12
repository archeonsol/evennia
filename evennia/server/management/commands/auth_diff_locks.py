"""Offline authorization differential for a bounded principal/resource matrix."""

from django.core.management.base import BaseCommand, CommandError

from evennia.authorization.migration import differential_replay
from evennia.server.management.commands.auth_migrate_locks import _queryset_for


class Command(BaseCommand):
    """Compare legacy and structured evaluators outside the game pulse."""

    help = "Replay both authorization evaluators over a bounded offline matrix."

    def add_arguments(self, parser):
        """Declare resource, principal, and operation samples."""

        parser.add_argument("kind", choices=("object", "account", "channel", "help", "script"))
        parser.add_argument("--principal", action="append", type=int, required=True)
        parser.add_argument("--access", action="append", required=True)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        """Run the matrix and fail when any decision diverges."""

        from evennia.accounts.models import AccountDB

        principals = list(AccountDB.objects.filter(pk__in=options["principal"]))
        if len(principals) != len(set(options["principal"])):
            raise CommandError("one or more principal accounts do not exist")
        limit = max(1, min(int(options["limit"]), 10_000))
        resources = list(_queryset_for(options["kind"])[:limit])
        results = differential_replay(resources, principals, options["access"])
        divergences = [result for result in results if not result.agrees]
        for result in divergences[:100]:
            self.stderr.write(
                f"{result.resource_ref} {result.principal_ref} {result.access_type}: "
                f"legacy={result.legacy_allowed} structured={result.structured_allowed} "
                f"reason={result.reason_code}"
            )
        self.stdout.write(
            f"authorization differential: decisions={len(results)} divergences={len(divergences)}"
        )
        if divergences:
            raise CommandError("authorization differential did not reach parity")
