from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scripts", "0019_backfill_paused_state_from_attributes"),
    ]

    operations = [
        migrations.AddField(
            model_name="scriptdb",
            name="db_attrs",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="JSONB attribute document. Replaces the db_attributes M2M when the JSONB backend is active.",
                verbose_name="attrs",
            ),
        ),
    ]
