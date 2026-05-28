from django.db import migrations


class Migration(migrations.Migration):
    """
    Compatibility shim for databases that already applied this migration name.

    The actual redundant Tag index removal is defined in
    0020_remove_redundant_tag_index. This no-op migration keeps existing
    generated merge migrations that depend on 0021 loadable.
    """

    dependencies = [
        ("typeclasses", "0020_remove_redundant_tag_index"),
    ]

    operations = []
