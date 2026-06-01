from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("scripts", "0021_alter_scriptdb_db_attrs"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS scripts_scriptdb_db_attrs_gin
                ON scripts_scriptdb USING GIN(db_attrs);
            """,
            reverse_sql="DROP INDEX IF EXISTS scripts_scriptdb_db_attrs_gin;",
        ),
    ]
