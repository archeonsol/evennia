"""
Backfill the new ScriptDB.db_paused_time / db_paused_callcount /
db_manually_paused columns from the old Attribute-based storage.

Migration 0018 promoted these from ``script.db._paused_time`` etc.
(persisted as Attribute rows on the ScriptDB) to first-class DB columns,
but didn't copy existing values. Any script that was paused at the time
of upgrade had its pause state stranded in Attribute rows, so the next
``unpause()`` treated the script as not-paused.

This migration copies the values across and then removes the orphan
Attribute rows so the data has one canonical home.
"""

from django.db import migrations

_OLD_ATTR_KEYS = ("_paused_time", "_paused_callcount", "_manually_paused")


def _attr_value(attr):
    """Best-effort load of a legacy Attribute's value.

    Handles both the pre-refactor pickle-only layout and the post-refactor
    typed-column layout, since this data migration may run against either.
    """
    val_type = getattr(attr, "db_val_type", "") or ""
    if val_type == "int":
        return attr.db_int_val
    if val_type == "bool":
        return bool(attr.db_int_val)
    if val_type == "float":
        return attr.db_float_val
    if val_type == "str":
        return attr.db_str_val
    if val_type == "none":
        return None
    raw = attr.db_value
    if raw is None:
        return None
    try:
        from evennia.utils.dbserialize import from_pickle

        return from_pickle(raw)
    except Exception:
        return None


def forward(apps, schema_editor):
    ScriptDB = apps.get_model("scripts", "ScriptDB")
    Attribute = apps.get_model("typeclasses", "Attribute")

    # All legacy pause attributes attached to any ScriptDB.
    attrs = Attribute.objects.filter(
        db_key__in=_OLD_ATTR_KEYS,
        db_model__iexact="scriptdb",
    ).prefetch_related("scriptdb_set")

    updates = {}  # scriptdb_pk -> dict of fields
    orphan_ids = []
    for attr in attrs:
        value = _attr_value(attr)
        for script in attr.scriptdb_set.all():
            payload = updates.setdefault(script.pk, {})
            if attr.db_key == "_paused_time":
                payload["db_paused_time"] = value
            elif attr.db_key == "_paused_callcount":
                payload["db_paused_callcount"] = value
            elif attr.db_key == "_manually_paused":
                payload["db_manually_paused"] = bool(value)
        orphan_ids.append(attr.pk)

    for pk, payload in updates.items():
        ScriptDB.objects.filter(pk=pk).update(**payload)

    if orphan_ids:
        Attribute.objects.filter(pk__in=orphan_ids).delete()


def backward(apps, schema_editor):
    # Best-effort reverse: rebuild the Attribute rows from the DB columns.
    # Lossy if anyone has cleared the new columns post-migration, but the
    # data is still in the DB columns either way.
    ScriptDB = apps.get_model("scripts", "ScriptDB")
    Attribute = apps.get_model("typeclasses", "Attribute")

    paused_scripts = ScriptDB.objects.filter(db_paused_time__isnull=False)
    for script in paused_scripts:
        if script.db_paused_time is not None:
            attr = Attribute.objects.create(
                db_key="_paused_time",
                db_model="scriptdb",
                db_value=None,
                db_val_type="float",
                db_float_val=script.db_paused_time,
            )
            script.db_attributes.add(attr)
        if script.db_paused_callcount is not None:
            attr = Attribute.objects.create(
                db_key="_paused_callcount",
                db_model="scriptdb",
                db_value=None,
                db_val_type="int",
                db_int_val=script.db_paused_callcount,
            )
            script.db_attributes.add(attr)
        if script.db_manually_paused:
            attr = Attribute.objects.create(
                db_key="_manually_paused",
                db_model="scriptdb",
                db_value=None,
                db_val_type="bool",
                db_int_val=1,
            )
            script.db_attributes.add(attr)


class Migration(migrations.Migration):

    dependencies = [
        ("scripts", "0018_scriptdb_pause_fields_and_start_delay_secs"),
        # Need the typed-column shape on Attribute for the value reader.
        ("typeclasses", "0019_attribute_typed_columns"),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=backward),
    ]
