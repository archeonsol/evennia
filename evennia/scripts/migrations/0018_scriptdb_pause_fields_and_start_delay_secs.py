"""
Add db_start_delay_secs (int), db_paused_time (float), db_paused_callcount (int),
and db_manually_paused (bool) to ScriptDB.

These promote formerly ndb-only pause state to first-class database columns so that
pause information survives idmapper cache eviction between ticks.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scripts", "0017_scriptbase_defaultscript_botstarter_dbprototype_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="scriptdb",
            name="db_start_delay_secs",
            field=models.IntegerField(
                default=0,
                help_text="Explicit start-delay in seconds. 0 = use db_interval, -1 = start immediately.",
                verbose_name="start delay secs",
            ),
        ),
        migrations.AddField(
            model_name="scriptdb",
            name="db_paused_time",
            field=models.FloatField(
                blank=True,
                null=True,
                help_text="Remaining seconds when script was paused (null = not paused).",
                verbose_name="paused time",
            ),
        ),
        migrations.AddField(
            model_name="scriptdb",
            name="db_paused_callcount",
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text="Task callcount captured when script was paused.",
                verbose_name="paused callcount",
            ),
        ),
        migrations.AddField(
            model_name="scriptdb",
            name="db_manually_paused",
            field=models.BooleanField(
                default=False,
                help_text="True when the script was paused explicitly via pause(), not auto-paused.",
                verbose_name="manually paused",
            ),
        ),
    ]
