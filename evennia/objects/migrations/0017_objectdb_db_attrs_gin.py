from django.db import migrations


class Migration(migrations.Migration):
    atomic = False  # CREATE INDEX CONCURRENTLY cannot run inside a transaction

    dependencies = [
        ("objects", "0016_alter_objectdb_db_attrs"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS objects_objectdb_db_attrs_gin
                ON objects_objectdb USING GIN(db_attrs);
            """,
            reverse_sql="DROP INDEX IF EXISTS objects_objectdb_db_attrs_gin;",
        ),
    ]
