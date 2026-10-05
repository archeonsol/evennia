"""The migrations that retire the GIN index on each ``db_attrs`` column."""

from importlib import import_module
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

CASES = (
    ("objects", "0020_drop_objectdb_db_attrs_gin", "objects_objectdb"),
    ("accounts", "0020_drop_accountdb_db_attrs_gin", "accounts_accountdb"),
    ("comms", "0028_drop_channeldb_db_attrs_gin", "comms_channeldb"),
    ("scripts", "0027_drop_scriptdb_db_attrs_gin", "scripts_scriptdb"),
)


def _editor(vendor):
    editor = mock.Mock()
    editor.connection.vendor = vendor
    return editor


class DropAttrsGinMigrationTests(SimpleTestCase):
    def _each(self):
        for app, name, table in CASES:
            with self.subTest(migration=f"{app}.{name}"):
                yield import_module(f"evennia.{app}.migrations.{name}"), table

    def test_postgres_drops_the_index_without_locking_writers(self):
        for module, table in self._each():
            editor = _editor("postgresql")

            module._drop(None, editor)

            editor.execute.assert_called_once_with(
                f"DROP INDEX CONCURRENTLY IF EXISTS {table}_db_attrs_gin;"
            )

    def test_reversing_puts_the_same_index_back(self):
        for module, table in self._each():
            editor = _editor("postgresql")

            module._create(None, editor)

            editor.execute.assert_called_once_with(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {table}_db_attrs_gin"
                f" ON {table} USING GIN(db_attrs);"
            )

    def test_the_name_matches_the_index_the_original_migration_made(self):
        # The drop has to hit the name the index was created under.
        originals = {
            "objects_objectdb": "evennia.objects.migrations.0017_objectdb_db_attrs_gin",
            "accounts_accountdb": "evennia.accounts.migrations.0016_accountdb_db_attrs_gin",
            "comms_channeldb": "evennia.comms.migrations.0026_channeldb_db_attrs_gin",
            "scripts_scriptdb": "evennia.scripts.migrations.0022_scriptdb_db_attrs_gin",
        }
        for module, table in self._each():
            made = _editor("postgresql")
            import_module(originals[table])._create(None, made)

            self.assertIn(f"{table}_db_attrs_gin", made.execute.call_args.args[0])
            self.assertEqual(module.INDEX, f"{table}_db_attrs_gin")

    def test_other_backends_never_had_the_index_so_do_nothing(self):
        for module, _table in self._each():
            for vendor in ("sqlite", "mysql"):
                editor = _editor(vendor)

                module._drop(None, editor)
                module._create(None, editor)

                editor.execute.assert_not_called()

    def test_each_runs_outside_a_transaction(self):
        # CONCURRENTLY refuses to run inside one.
        for module, _table in self._each():
            migration = module.Migration
            self.assertFalse(migration.atomic)
            self.assertTrue(all(op.atomic is False for op in migration.operations))

    def test_each_follows_a_migration_that_exists(self):
        # Not the loader: objects.0007 queries the database when it is imported.
        for module, _table in self._each():
            ((parent_app, parent_name),) = module.Migration.dependencies

            package = import_module(f"evennia.{parent_app}.migrations")
            parent = Path(package.__file__).parent / f"{parent_name}.py"

            self.assertTrue(parent.exists(), parent_name)
