from django.db import migrations


class Migration(migrations.Migration):
    # No-op: help_text was the only difference from 0024; absorbed there.
    # Kept so existing django_migrations records still resolve.

    dependencies = [
        ("comms", "0024_channeldb_db_attrs"),
    ]

    operations = []
