"""Compile legacy lock storage for one resource kind."""

from django.core.management.base import BaseCommand, CommandError

from evennia.authorization.compiler import CompilationError, compile_lockstring
from evennia.authorization.migration import migrate_resource


def _queryset_for(kind: str):
    """Return a deterministic queryset for a supported resource kind."""

    if kind == "object":
        from evennia.objects.models import ObjectDB

        return ObjectDB.objects.order_by("pk")
    if kind == "account":
        from evennia.accounts.models import AccountDB

        return AccountDB.objects.order_by("pk")
    if kind == "channel":
        from evennia.comms.models import ChannelDB

        return ChannelDB.objects.order_by("pk")
    if kind == "help":
        from evennia.help.models import HelpEntry

        return HelpEntry.objects.order_by("pk")
    if kind == "script":
        from evennia.scripts.models import ScriptDB

        return ScriptDB.objects.order_by("pk")
    raise CommandError(f"unsupported resource kind {kind!r}")


class Command(BaseCommand):
    """Dry-run or apply a finite per-kind lock migration."""

    help = "Compile lockstrings into structured authorization policies."

    def add_arguments(self, parser):
        """Declare finite migration controls."""

        parser.add_argument("kind", choices=("object", "account", "channel", "help", "script"))
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--freeze", action="store_true")
        parser.add_argument("--limit", type=int, default=1000)

    def handle(self, *args, **options):
        """Compile a bounded resource page and print explicit failures."""

        limit = max(1, min(int(options["limit"]), 100_000))
        resources = _queryset_for(options["kind"])[:limit]
        inspected = migrated = failures = 0
        warnings = 0
        for resource in resources:
            inspected += 1
            source = str(getattr(resource, "lock_storage", "") or "")
            if not source:
                continue
            try:
                if options["apply"]:
                    result = migrate_resource(resource, freeze=options["freeze"])
                    migrated += 1
                    for warning in result.warnings:
                        warnings += 1
                        self.stderr.write(f"warning {options['kind']}:{resource.pk}: {warning}")
                else:
                    result = compile_lockstring(
                        source,
                        resource_ref=f"{options['kind']}:{resource.pk}",
                    )
                    for warning in result.warnings:
                        warnings += 1
                        self.stderr.write(f"warning {options['kind']}:{resource.pk}: {warning}")
            except CompilationError as err:
                failures += 1
                self.stderr.write(f"{options['kind']}:{resource.pk}: {err}")
        mode = "applied" if options["apply"] else "dry-run"
        self.stdout.write(
            f"authorization migration {mode}: inspected={inspected} "
            f"migrated={migrated} failures={failures} warnings={warnings}"
        )
        if failures:
            raise CommandError("migration contains unsupported lock semantics")
