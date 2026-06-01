from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("objects", "0014_defaultobject_defaultcharacter_defaultexit_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="objectdb",
            name="db_attrs",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="JSONB attribute document. One row per object; replaces the db_attributes M2M when the JSONB backend is active.",
                verbose_name="attrs",
            ),
        ),
    ]
