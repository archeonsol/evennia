from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add typed value columns to Attribute for int/bool/float/str/None.
    Avoids pickle round-trip for the most common game attribute types.
    Existing rows keep db_val_type='' and continue using db_value (pickle).
    """

    dependencies = [
        (
            "typeclasses",
            "0018_rename_tag_db_key_db_category_db_tagtype_db_model_typeclasses_db_key_be0c81_idx",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="attribute",
            name="db_val_type",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Type discriminator for typed columns: int/bool/float/str/none/'' (pickle).",
                max_length=8,
                verbose_name="val_type",
            ),
        ),
        migrations.AddField(
            model_name="attribute",
            name="db_int_val",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="int_val"),
        ),
        migrations.AddField(
            model_name="attribute",
            name="db_float_val",
            field=models.FloatField(blank=True, null=True, verbose_name="float_val"),
        ),
        migrations.AddField(
            model_name="attribute",
            name="db_str_val",
            field=models.TextField(blank=True, null=True, verbose_name="str_val"),
        ),
    ]
