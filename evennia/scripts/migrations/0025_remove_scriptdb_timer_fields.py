"""
Drop the Script.interval timer machinery's database columns.

Script is now a storage-only typeclass with no timer component, so the
per-Script timer state (interval, repeats, start-delay, is-active and pause
bookkeeping) no longer has any reader or writer. The TimeScript proxy
(formerly `evennia.utils.gametime.TimeScript`) is removed along with the
typeclass it decorated.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("scripts", "0024_shopregistryscript"),
    ]

    operations = [
        migrations.DeleteModel(
            name="TimeScript",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_interval",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_is_active",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_manually_paused",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_paused_callcount",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_paused_time",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_repeats",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_start_delay",
        ),
        migrations.RemoveField(
            model_name="scriptdb",
            name="db_start_delay_secs",
        ),
    ]
