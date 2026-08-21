"""Migration state.

``migrate`` runs automatically on ``@reload``, which makes "what is about to
run" a question worth being able to answer *before* pressing reload rather
than afterwards from a log file.

Read-only on purpose. The console reports what is pending; it does not apply
migrations. Applying one is a deploy step with its own failure modes and its
own rollback story, and a web button is the wrong place for it.

"""

from __future__ import annotations

from evennia.console.registry import Panel


class MigrationsPanel(Panel):
    """Applied and unapplied migrations, plus a missing-migration check."""

    key = "migrations"
    label = "Migrations"
    description = "What migrate would apply on the next reload."
    columns = ("app", "name", "applied")
    needs_io = False

    def rows(self, ctx):
        """Return the migration graph's current state.

        Returns:
            dict: Per-app migration rows, the pending list, and whether any
            model changed without a migration being generated.
        """

        from django.db import connections
        from django.db.migrations.loader import MigrationLoader

        connection = connections["default"]
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        applied = set(loader.applied_migrations or ())

        rows = []
        pending = []
        for app_label, name in sorted(loader.disk_migrations):
            is_applied = (app_label, name) in applied
            rows.append({"app": app_label, "name": name, "applied": is_applied})
            if not is_applied:
                pending.append({"app": app_label, "name": name})

        return {
            "rows": rows,
            "pending": pending,
            "pending_count": len(pending),
            "missing": self._missing_migrations(loader),
        }

    def _missing_migrations(self, loader):
        """Return apps whose models changed with no migration generated.

        A real recurring failure mode, and silent until deploy: the model
        change works locally against an already-migrated database and fails on
        a fresh one.
        """

        try:
            from django.db.migrations.autodetector import MigrationAutodetector
            from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
            from django.db.migrations.state import ProjectState

            autodetector = MigrationAutodetector(
                loader.project_state(),
                ProjectState.from_apps(_apps()),
                NonInteractiveMigrationQuestioner(specified_apps=set(), dry_run=True),
            )
            changes = autodetector.changes(graph=loader.graph)
        except Exception:  # noqa: BLE001 - a report must never break the page
            return {"available": False, "apps": []}
        return {
            "available": True,
            "apps": sorted(
                {app_label: len(migrations) for app_label, migrations in changes.items()}.items()
            ),
        }


def _apps():
    """Return the app registry. Isolated so the import stays lazy."""

    from django.apps import apps

    return apps
