from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("comms", "0025_alter_channeldb_db_attrs"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS comms_channeldb_db_attrs_gin
                ON comms_channeldb USING GIN(db_attrs);
            """,
            reverse_sql="DROP INDEX IF EXISTS comms_channeldb_db_attrs_gin;",
        ),
    ]
