"""Django admin mutations are mirrored into the console audit trail.

The admin and the console coexist for a while. A console timeline that shows
only console activity would be misleading in exactly the situation it is most
needed -- working out who changed something -- so the admin's audit path
mirrors into it.

The mirror is best-effort by design: the mutation has already happened by the
time it runs, so it must never raise, and a failure has to surface as the
admin's existing audit warning rather than as an error.

"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from evennia.console import audit
from evennia.console.models import ConsoleAuditEvent
from evennia.objects.models import ObjectDB
from evennia.web.admin.mixins import OwnerSafeModelAdminMixin


class _Mirror(OwnerSafeModelAdminMixin):
    """Minimal host exposing the mirror with a concrete model."""

    model = ObjectDB

    def __init__(self):
        pass


def _request(pk=1, username="staff"):
    """Build the smallest request the mirror reads."""
    return SimpleNamespace(user=SimpleNamespace(pk=pk, username=username))


class TestAdminAuditMirror(TestCase):
    """Admin add, change, and delete all reach the console trail."""

    def setUp(self):
        self.mirror = _Mirror()

    def test_mirror_writes_a_console_row(self):
        request = _request()
        self.mirror._mirror_console_audit(request, "change", object_id=42, repr_text="a room")
        row = ConsoleAuditEvent.objects.get()
        self.assertEqual(row.panel, "admin")
        self.assertEqual(row.operation, "change")
        self.assertEqual(row.actor_id, 1)
        self.assertEqual(row.actor_name, "staff")
        self.assertEqual(row.target_ref, "objects.objectdb#42")
        self.assertEqual(row.message, "a room")

    def test_mirror_records_no_warning_on_success(self):
        request = _request()
        self.mirror._mirror_console_audit(request, "add", object_id=1)
        self.assertFalse(getattr(request, "_evennia_admin_audit_warning", False))

    def test_failed_mirror_raises_a_warning_not_an_exception(self):
        request = _request()
        original = audit.record
        audit.record = lambda **kwargs: None
        try:
            self.mirror._mirror_console_audit(request, "delete", object_id=9)
        finally:
            audit.record = original
        self.assertTrue(request._evennia_admin_audit_warning)

    def test_missing_target_is_recorded_without_a_ref(self):
        self.mirror._mirror_console_audit(_request(), "change")
        self.assertEqual(ConsoleAuditEvent.objects.get().target_ref, "")

    def test_admin_rows_prune_on_the_normal_window(self):
        self.mirror._mirror_console_audit(_request(), "change", object_id=1)
        self.assertEqual(ConsoleAuditEvent.objects.get().retention, "normal")


class TestActorIsDenormalized(TestCase):
    """The actor is stored by id and name, never by foreign key.

    Asserted structurally rather than by deleting an account: an audit row has
    to outlive the account it describes, and the way that is guaranteed is the
    absence of a relation, not the behaviour of one particular cascade.
    """

    def test_actor_columns_are_not_relations(self):
        fields = {field.name: field for field in ConsoleAuditEvent._meta.concrete_fields}
        self.assertIsNone(fields["actor_id"].remote_field)
        self.assertIsNone(fields["actor_name"].remote_field)

    def test_no_field_relates_to_the_account_model(self):
        account_label = get_user_model()._meta.label_lower
        for model_field in ConsoleAuditEvent._meta.get_fields():
            related = getattr(model_field, "related_model", None)
            if related is not None:
                self.assertNotEqual(related._meta.label_lower, account_label)

    def test_a_real_actor_is_recorded_by_value(self):
        account = get_user_model().objects.create(username="mirror-tester")
        _Mirror()._mirror_console_audit(
            SimpleNamespace(user=account), "change", object_id=3, repr_text="thing"
        )
        row = ConsoleAuditEvent.objects.get()
        self.assertEqual(row.actor_id, account.pk)
        self.assertEqual(row.actor_name, "mirror-tester")
