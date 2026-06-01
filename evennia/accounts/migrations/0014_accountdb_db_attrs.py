from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_defaultaccount_account_bot_defaultguest_discordbot_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountdb",
            name="db_attrs",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="JSONB attribute document. Replaces the db_attributes M2M when the JSONB backend is active.",
                verbose_name="attrs",
            ),
        ),
    ]
