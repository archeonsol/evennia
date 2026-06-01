from django.db import migrations


def _create(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS scripts_scriptdb_db_attrs_gin"
        " ON scripts_scriptdb USING GIN(db_attrs);"
    )


def _drop(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS scripts_scriptdb_db_attrs_gin;")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("scripts", "0021_alter_scriptdb_db_attrs"),
    ]

    operations = [
        migrations.RunPython(_create, _drop, atomic=False),
    ]
