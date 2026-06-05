import django.db.models.deletion
from django.db import migrations, models

CONTROL_ACCOUNT = "account"


def strip_floor(apps, schema_editor):
    """F2: the controller account floor is now derived, not stored.

    Remove **every** ``["account", id]`` entry from every focus stack so the
    stack holds only the bodies above the floor. Stripping all account entries
    (not just the leading one) also repairs the ``.67``-``.70`` ``_ensure_floor``
    bug, where an ownership transfer could ``insert(0, floor)`` over a stale
    floor and leave a doubled ``[["account", B], ["account", A], ...]`` stack;
    a leftover mid-stack account entry would otherwise be mis-resolved as a body.
    Idempotent: a stack with no account entries is left unchanged.
    """
    ControlBinding = apps.get_model("accounts", "ControlBinding")
    for binding in ControlBinding.objects.all().iterator():
        stack = binding.db_focus_stack or []
        cleaned = [entry for entry in stack if not (entry and entry[0] == CONTROL_ACCOUNT)]
        if cleaned != stack:
            binding.db_focus_stack = cleaned
            binding.save(update_fields=["db_focus_stack"])


def restore_floor(apps, schema_editor):
    """Reverse: re-prepend the floor entry as ``.70`` stored it.

    ``.70`` stored the floor as ``["account", binding.db_account_id]`` — i.e. the
    *controller*, which `for_identity` may have repointed at a possessing driver
    rather than the identity's owner. Reconstructing from ``db_account`` here is
    faithful to that on-disk format; it does not (and cannot) recover an original
    owner, since ``.71`` deliberately stopped conflating controller with owner.
    """
    ControlBinding = apps.get_model("accounts", "ControlBinding")
    for binding in ControlBinding.objects.all().iterator():
        stack = binding.db_focus_stack or []
        if not stack or stack[0][0] != CONTROL_ACCOUNT:
            binding.db_focus_stack = [[CONTROL_ACCOUNT, binding.db_account_id]] + stack
            binding.save(update_fields=["db_focus_stack"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_controlbinding"),
    ]

    operations = [
        migrations.AlterField(
            model_name="controlbinding",
            name="db_account",
            field=models.ForeignKey(
                help_text="The account currently driving this graph (the stack floor).",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="control_bindings",
                to="accounts.accountdb",
            ),
        ),
        migrations.AlterField(
            model_name="controlbinding",
            name="db_focus_stack",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Ordered [[kind, id], ...] of bodies ABOVE the floor; top=active body.",
            ),
        ),
        migrations.AlterField(
            model_name="controlbinding",
            name="db_identity",
            field=models.OneToOneField(
                blank=True,
                help_text="The root driven body (character/NPC) this graph is anchored on.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="control_binding",
                to="objects.objectdb",
            ),
        ),
        migrations.RunPython(strip_floor, restore_floor),
    ]
