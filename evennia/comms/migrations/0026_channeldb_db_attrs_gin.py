from django.db import migrations


def _create(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS comms_channeldb_db_attrs_gin"
        " ON comms_channeldb USING GIN(db_attrs);"
    )


def _drop(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS comms_channeldb_db_attrs_gin;")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("comms", "0025_alter_channeldb_db_attrs"),
    ]

    operations = [
        migrations.RunPython(_create, _drop, atomic=False),
    ]
