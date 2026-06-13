"""
Delete the orphaned ShopRegistryScript proxy model.

The proxy was committed without a backing typeclass, so it has no reader or
writer in the engine and every `makemigrations scripts` run wants to remove
it. Dropping the model is state-only: a proxy carries no table, so there is
no database change.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("scripts", "0025_remove_scriptdb_timer_fields"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ShopRegistryScript",
        ),
    ]
