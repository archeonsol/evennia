# Generated for Underspire Tier 2 event bus + job queue

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("server", "0003_alter_serverconfig_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="GameEvent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject", models.CharField(db_index=True, max_length=128)),
                ("payload_json", models.TextField(default="{}")),
                ("actor_ref", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "verbose_name": "Game event",
                "verbose_name_plural": "Game events",
            },
        ),
        migrations.CreateModel(
            name="EngineJob",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("job_id", models.CharField(db_index=True, max_length=32, unique=True)),
                ("job_type", models.CharField(db_index=True, max_length=64)),
                ("payload_json", models.TextField(default="{}")),
                ("priority", models.IntegerField(db_index=True, default=0)),
                ("status", models.CharField(db_index=True, default="pending", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Engine job",
                "verbose_name_plural": "Engine jobs",
            },
        ),
        migrations.AddIndex(
            model_name="gameevent",
            index=models.Index(fields=["subject", "created_at"], name="server_game_subject_0e8f0d_idx"),
        ),
    ]
