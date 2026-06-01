from django.db import migrations


class Migration(migrations.Migration):
    # No-op: help_text was the only difference from 0015; absorbed there.
    # Kept so existing django_migrations records still resolve.

    dependencies = [
        ("objects", "0015_objectdb_db_attrs"),
    ]

    operations = []
