from django.db import migrations


class Migration(migrations.Migration):
    # No-op: help_text was the only difference from 0020; absorbed there.
    # Kept so existing django_migrations records still resolve.

    dependencies = [
        ("scripts", "0020_scriptdb_db_attrs"),
    ]

    operations = []
