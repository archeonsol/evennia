from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0015_alter_accountdb_db_attrs"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_accountdb_db_attrs_gin
                ON accounts_accountdb USING GIN(db_attrs);
            """,
            reverse_sql="DROP INDEX IF EXISTS accounts_accountdb_db_attrs_gin;",
        ),
    ]
