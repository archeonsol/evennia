from django.db import migrations


class Migration(migrations.Migration):
    """
    Drop the explicit composite index on Tag(db_key, db_category, db_tagtype, db_model)
    that was redundant with the unique_together constraint.  The unique constraint
    already provides a B-tree index on these four columns; the second index wastes
    space and doubles write overhead on every tag insert/update/delete.
    """

    dependencies = [
        ("typeclasses", "0019_attribute_typed_columns"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="tag",
            name="typeclasses_db_key_be0c81_idx",
        ),
    ]
