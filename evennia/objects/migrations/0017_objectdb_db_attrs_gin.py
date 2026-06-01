from django.db import migrations


def _create(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS objects_objectdb_db_attrs_gin"
        " ON objects_objectdb USING GIN(db_attrs);"
    )


def _drop(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS objects_objectdb_db_attrs_gin;")


class Migration(migrations.Migration):
    atomic = False  # CREATE INDEX CONCURRENTLY cannot run inside a transaction

    dependencies = [
        ("objects", "0016_alter_objectdb_db_attrs"),
    ]

    operations = [
        migrations.RunPython(_create, _drop, atomic=False),
    ]
