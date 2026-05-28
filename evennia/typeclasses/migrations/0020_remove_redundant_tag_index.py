from django.db import migrations


class Migration(migrations.Migration):
    """
    Drop the explicit composite index on Tag(db_key, db_category, db_tagtype, db_model)
    that was redundant with the unique_together constraint. The unique constraint
    already provides a B-tree index on these four columns; the second index wastes
    space and doubles write overhead on every tag insert/update/delete.

    Uses ``SeparateDatabaseAndState`` with ``DROP INDEX IF EXISTS`` so the
    migration is tolerant of databases whose history landed when
    ``0017_use_index_instead_of_index_together_in_tags`` was its earlier
    no-op revision. On those DBs the index was never created in the first
    place; the plain ``RemoveIndex`` op raised because there was nothing
    to drop. ``state_operations`` keeps Django's model state aligned so
    later migrations and ``makemigrations`` don't see a phantom index.
    """

    dependencies = [
        ("typeclasses", "0019_attribute_typed_columns"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(
                    model_name="tag",
                    name="typeclasses_db_key_be0c81_idx",
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX IF EXISTS typeclasses_db_key_be0c81_idx",
                    reverse_sql=(
                        "CREATE INDEX IF NOT EXISTS typeclasses_db_key_be0c81_idx "
                        "ON typeclasses_tag (db_key, db_category, db_tagtype, db_model)"
                    ),
                ),
            ],
        ),
    ]
