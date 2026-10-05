"""Drop the GIN index on ``scripts_scriptdb.db_attrs``.

The index was added with the JSONB attribute column so that ``db_attrs @> ...``
containment could find objects by attribute value. In production it has been
read a few dozen times in its life, from staff tools, a boot-time repair and the
rental door lookup, and each of those is a sequential scan of about ten
milliseconds without it. It is paid for on every write instead: while any index
covers ``db_attrs``, no update of that column can be a heap-only tuple, so the
write-behind flush rewrites the row and adds an entry to the index each time,
and the index grew to some forty times the size of the table it serves.
(``docs/performance.md`` in the game repo has the measurement.)

PostgreSQL only; other backends never had the index. The reverse recreates it.
"""

from django.db import migrations

INDEX = "scripts_scriptdb_db_attrs_gin"
TABLE = "scripts_scriptdb"


def _drop(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX};")


def _create(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} USING GIN(db_attrs);"
    )


class Migration(migrations.Migration):
    atomic = False  # DROP / CREATE INDEX CONCURRENTLY cannot run inside a transaction

    dependencies = [
        ("scripts", "0026_delete_shopregistryscript"),
    ]

    operations = [
        migrations.RunPython(_drop, _create, atomic=False),
    ]
