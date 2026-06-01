from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0023_defaultchannel_channel_assistchannel"),
    ]

    operations = [
        migrations.AddField(
            model_name="channeldb",
            name="db_attrs",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="JSONB attribute document. Replaces the db_attributes M2M when the JSONB backend is active.",
                verbose_name="attrs",
            ),
        ),
    ]
