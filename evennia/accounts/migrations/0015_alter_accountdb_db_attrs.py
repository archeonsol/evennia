from django.db import migrations


class Migration(migrations.Migration):
    # No-op: help_text was the only difference from 0014; absorbed there.
    # Kept so existing django_migrations records still resolve.

    dependencies = [
        ("accounts", "0014_accountdb_db_attrs"),
    ]

    operations = []
