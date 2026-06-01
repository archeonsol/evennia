from django.db import migrations


def _create(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_accountdb_db_attrs_gin"
        " ON accounts_accountdb USING GIN(db_attrs);"
    )


def _drop(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS accounts_accountdb_db_attrs_gin;")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0015_alter_accountdb_db_attrs"),
    ]

    operations = [
        migrations.RunPython(_create, _drop, atomic=False),
    ]
