from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add composite indexes over (db_val_type, value_col) for the typed value
    columns introduced in 0019. Every value-equality query pins db_val_type
    via value_query_filter, so the discriminator-first composite lets the
    planner do an index range scan instead of a full sequential scan over
    the Attribute table.

    db_str_val is intentionally not indexed here. TextField btree entries
    on PostgreSQL can overflow the page size for long values (e.g. game
    descriptions or large JSON-serialized containers). A partial or hash
    index can be added in a follow-up once a real callsite warrants it.
    """

    dependencies = [
        ("typeclasses", "0021_remove_redundant_tag_index"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="attribute",
            index=models.Index(
                fields=["db_val_type", "db_int_val"],
                name="attr_valtype_int_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="attribute",
            index=models.Index(
                fields=["db_val_type", "db_float_val"],
                name="attr_valtype_float_idx",
            ),
        ),
    ]
