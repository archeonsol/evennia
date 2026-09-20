# Generated for the .255 authorization model hardening arc.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("server", "0011_sessionrecord_tls_signals"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuthorizationPrincipalGroup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("group_ref", models.CharField(max_length=128, unique=True)),
                ("label", models.CharField(blank=True, default="", max_length=255)),
                ("category", models.CharField(blank=True, default="", max_length=64)),
                ("metadata", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["category", "group_ref"],
                        name="authgroup_category_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AuthorizationGroupMembership",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("group_ref", models.CharField(max_length=128)),
                ("principal_ref", models.CharField(max_length=128)),
                (
                    "provenance",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["principal_ref"],
                        name="authgroup_principal_idx",
                    ),
                    models.Index(fields=["group_ref"], name="authgroup_group_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("group_ref", "principal_ref"),
                        name="authgroup_membership_uniq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="AuthorizationPolicyBundle",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.PositiveIntegerField(unique=True)),
                ("document", models.JSONField(default=dict)),
                ("active", models.BooleanField(default=False)),
                (
                    "provenance",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["active", "-version"],
                        name="authbundle_active_idx",
                    ),
                ],
            },
        ),
    ]
