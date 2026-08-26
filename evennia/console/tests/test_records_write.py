"""Tests for creating and changing rows through the records lens.

The coercion tests carry most of the weight. A browser sends JSON, the mutation
service compares exact types, and everything between those two facts is a place
where a value can be quietly turned into the wrong thing. A bool silently
becoming an int, or a datetime silently losing its time, is worse than a
refusal.

"""

from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.coerce import CoercionError, coerce_field, coerce_payload
from evennia.console.panels.records import RecordsPanel
from evennia.console.registry import CONSOLE_ACCESS, IOContext, WorkerContext
from evennia.console.services import AdminMutationResult
from evennia.help.models import HelpEntry


def _worker(**params):
    """Build a worker context."""
    return WorkerContext(
        actor_id=1, actor_name="t", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


def _io(actor_id=1):
    """Build an IO context for one acting account."""
    return IOContext(
        actor_id=actor_id, actor_name="tester", capabilities=frozenset({CONSOLE_ACCESS})
    )


class ActingTestCase(TestCase):
    """A real acting account.

    The mutation service reloads the actor and recomputes permission from
    fresh state on every call, so a fabricated id is correctly refused. That
    check is the point of the service; the tests have to satisfy it rather
    than work around it.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.panel = RecordsPanel()
        self.actor = get_user_model().objects.create(
            username="record-writer", is_active=True, is_staff=True, is_superuser=True
        )

    def io(self):
        """Return an IO context for the acting account."""
        return _io(self.actor.pk)


class TestCoercion(TestCase):
    """JSON in, exact types out, or a sentence saying why not."""

    def test_exact_type_passes_through(self):
        self.assertIs(coerce_field("f", True, (bool,)), True)
        self.assertEqual(coerce_field("f", 5, (int,)), 5)

    def test_bool_is_not_routed_through_the_int_coercer(self):
        # bool subclasses int, and the service compares exact types, so an
        # order-of-checks mistake here stores True as 1.
        value = coerce_field("f", True, (bool, int))
        self.assertIs(type(value), bool)

    def test_bool_words(self):
        for text in ("true", "YES", "on", "1"):
            self.assertIs(coerce_field("f", text, (bool,)), True)
        for text in ("false", "no", "off", "0", ""):
            self.assertIs(coerce_field("f", text, (bool,)), False)

    def test_bool_rejects_nonsense(self):
        with self.assertRaises(CoercionError):
            coerce_field("f", "banana", (bool,))

    def test_numbers(self):
        self.assertEqual(coerce_field("f", "42", (int,)), 42)
        self.assertEqual(coerce_field("f", "1.5", (float,)), 1.5)
        self.assertEqual(coerce_field("f", "1.50", (Decimal,)), Decimal("1.50"))

    def test_number_rejects_nonsense(self):
        for accepted in ((int,), (float,), (Decimal,)):
            with self.assertRaises(CoercionError):
                coerce_field("f", "not a number", accepted)

    def test_datetime_is_made_aware(self):
        value = coerce_field("f", "2026-08-19T12:00:00", (datetime,))
        self.assertIs(type(value), datetime)
        self.assertFalse(timezone.is_naive(value))

    def test_datetime_beats_date_when_both_are_accepted(self):
        # A datetime is a date. Checking date first would silently drop the
        # time part of every timestamp submitted.
        value = coerce_field("f", "2026-08-19T12:30:00", (datetime, date))
        self.assertIs(type(value), datetime)
        self.assertEqual(value.hour, 12)

    def test_date_and_time(self):
        self.assertEqual(coerce_field("f", "2026-08-19", (date,)), date(2026, 8, 19))
        self.assertEqual(coerce_field("f", "13:45", (time,)), time(13, 45))

    def test_uuid(self):
        text = "0f2b7f1e-1c4a-4f0e-9a3b-2f5e6d7c8a9b"
        self.assertEqual(coerce_field("f", text, (UUID,)), UUID(text))

    def test_null_is_allowed_only_where_declared(self):
        self.assertIsNone(coerce_field("f", None, (str, type(None))))
        with self.assertRaises(CoercionError) as caught:
            coerce_field("f", None, (str,))
        self.assertIn("cannot be empty", str(caught.exception))

    def test_containers_are_frozen(self):
        from evennia.console.services import FrozenContainer

        value = coerce_field("f", {"a": 1}, (FrozenContainer,))
        self.assertIsInstance(value, FrozenContainer)

    def test_error_names_the_field(self):
        with self.assertRaises(CoercionError) as caught:
            coerce_field("db_date_created", "nope", (datetime,))
        self.assertEqual(caught.exception.field, "db_date_created")
        self.assertIn("db_date_created", str(caught.exception))

    def test_payload_rejects_an_unknown_field(self):
        with self.assertRaises(CoercionError) as caught:
            coerce_payload({"nope": 1}, {"db_key": (str,)})
        self.assertIn("cannot be written", str(caught.exception))

    def test_payload_is_sorted_for_a_stable_request(self):
        concrete = coerce_payload({"b": "2", "a": "1"}, {"a": (str,), "b": (str,)})
        self.assertEqual([name for name, _ in concrete], ["a", "b"])


class TestForm(TestCase):
    """The form descriptor states what may be written before anything is."""

    def setUp(self):
        self.panel = RecordsPanel()

    def test_lists_only_writable_fields(self):
        result = self.panel.form(_worker(), model="help.helpentry")
        self.assertTrue(result["writable"])
        names = {field["name"] for field in result["fields"]}
        self.assertIn("db_key", names)

    def test_marks_nullable_fields(self):
        fields = {
            f["name"]: f for f in self.panel.form(_worker(), model="help.helpentry")["fields"]
        }
        self.assertTrue(fields["db_lock_storage"]["nullable"])

    def test_a_domain_owned_model_offers_no_form(self):
        result = self.panel.form(_worker(), model="server.sanction")
        self.assertFalse(result["writable"])
        self.assertEqual(result["fields"], [])
        self.assertIn("issue_sanction", result["write_via"])

    def test_names_every_specialized_write_control(self):
        result = self.panel.form(_worker(), model="accounts.accountdb")
        self.assertNotIn("password", result["unsupported"])
        self.assertTrue(result["supports_password"])
        self.assertTrue(result["supports_tags"])
        self.assertEqual(
            {item["name"] for item in result["relations"]}, {"groups", "user_permissions"}
        )


class TestSave(ActingTestCase):
    """Creating and changing rows, and refusing what this lens must not do."""

    def test_creates_a_row(self):
        result = self.panel.save(
            self.io(),
            model="help.helpentry",
            values={
                "db_key": "console-created",
                "db_entrytext": "body",
                "db_help_category": "general",
                "db_lock_storage": "",
            },
        )
        self.assertEqual(result["status"], "created")
        self.assertTrue(HelpEntry.objects.filter(db_key="console-created").exists())

    def test_changes_a_row(self):
        entry = HelpEntry.objects.create(
            db_key="existing", db_entrytext="before", db_help_category="general"
        )
        result = self.panel.save(
            self.io(), model="help.helpentry", pk=entry.pk, values={"db_entrytext": "after"}
        )
        self.assertEqual(result["status"], "changed")
        stored = HelpEntry.objects.filter(pk=entry.pk).values_list("db_entrytext", flat=True)
        self.assertEqual(stored.first(), "after")

    def test_refuses_a_domain_owned_model(self):
        with self.assertRaises(PermissionDenied) as caught:
            self.panel.save(self.io(), model="server.sanction", values={"level": "ban"})
        self.assertIn("issue_sanction", str(caught.exception))

    def test_refuses_a_password(self):
        with self.assertRaises(PermissionDenied) as caught:
            self.panel.save(self.io(), model="accounts.accountdb", values={"password": "x"})
        self.assertIn("password service", str(caught.exception))

    def test_refuses_an_unknown_field(self):
        with self.assertRaises(CoercionError):
            self.panel.save(self.io(), model="help.helpentry", values={"nope": "x"})

    def test_refuses_an_uncoercible_value(self):
        with self.assertRaises(CoercionError):
            self.panel.save(
                self.io(), model="accounts.accountdb", values={"last_login": "not a date"}
            )

    def test_console_capability_does_not_depend_on_django_staff_flags(self):
        self.actor.is_staff = False
        self.actor.is_superuser = False
        self.actor.save(update_fields=["is_staff", "is_superuser"])
        result = self.panel.save(
            self.io(),
            model="help.helpentry",
            values={
                "db_key": "capability-owned",
                "db_entrytext": "body",
                "db_help_category": "general",
            },
        )
        self.assertEqual(result["status"], "created")

    def test_console_capability_can_change_django_authority_fields(self):
        self.actor.is_staff = False
        self.actor.is_superuser = False
        self.actor.save(update_fields=["is_staff", "is_superuser"])
        target = type(self.actor).objects.create(username="authority-target", is_active=True)
        result = self.panel.save(
            self.io(),
            model="accounts.accountdb",
            pk=target.pk,
            values={"is_staff": True},
        )
        target.refresh_from_db()
        self.assertEqual(result["status"], "changed")
        self.assertTrue(target.is_staff)

    def test_console_can_set_an_account_password_without_django_staff_flags(self):
        target = type(self.actor).objects.create(username="password-target", is_active=True)
        self.actor.is_staff = False
        self.actor.is_superuser = False
        self.actor.save(update_fields=["is_staff", "is_superuser"])
        result = self.panel.set_password(
            self.io(), account_id=target.pk, password="N3w-passphrase!123"
        )
        target.refresh_from_db()
        self.assertEqual(result["status"], "changed")
        self.assertTrue(target.check_password("N3w-passphrase!123"))
        row = ConsoleAuditEvent.objects.get(operation="password_set")
        self.assertNotIn("N3w-passphrase", str(row.after))

    def test_account_creation_uses_the_presence_gated_secret_action(self):
        result = self.panel.create_account(
            self.io(),
            values={"username": "console-created-account", "email": "created@example.test"},
            relations={"groups": [], "user_permissions": []},
            tags=[],
            password="Violet-Quasar-4839!",
        )
        self.assertEqual(result["status"], "created", result)
        account = type(self.actor).objects.get(pk=result["object_id"])
        self.assertTrue(account.check_password("Violet-Quasar-4839!"))
        row = ConsoleAuditEvent.objects.get(operation="add")
        self.assertNotIn("Violet-Quasar", str(row.after))

    def test_generic_save_cannot_bypass_account_creation_presence_check(self):
        with self.assertRaisesRegex(ValueError, "presence-gated"):
            self.panel.save(self.io(), model="accounts.accountdb", values={"username": "bypass"})

    def test_bulk_change_is_previewed_before_it_is_applied(self):
        first = HelpEntry.objects.create(
            db_key="bulk-a", db_entrytext="before", db_help_category="general"
        )
        second = HelpEntry.objects.create(
            db_key="bulk-b", db_entrytext="before", db_help_category="general"
        )
        preview = self.panel.preview_change(
            _worker(),
            model="help.helpentry",
            ids=[first.pk, second.pk],
            values={"db_help_category": "staff"},
        )
        self.assertEqual(len(preview["rows"]), 2)
        self.assertEqual(preview["rows"][0]["after"]["db_help_category"], "staff")
        self.assertFalse(
            HelpEntry.objects.filter(
                pk__in=[first.pk, second.pk], db_help_category="staff"
            ).exists()
        )

        result = self.panel.bulk_save(
            self.io(),
            model="help.helpentry",
            ids=[first.pk, second.pk],
            values={"db_help_category": "staff"},
            reason="move both entries to the staff category",
        )
        self.assertEqual(result["changed"], 2)
        self.assertEqual(
            HelpEntry.objects.filter(
                pk__in=[first.pk, second.pk], db_help_category="staff"
            ).count(),
            2,
        )
        self.assertEqual(
            set(
                ConsoleAuditEvent.objects.filter(operation="change").values_list(
                    "message", flat=True
                )
            ),
            {"move both entries to the staff category"},
        )

    def test_relation_search_and_graph_return_navigable_rows(self):
        from evennia.utils.create import create_object

        room = create_object(key="Searchable Room", nohome=True)
        child = create_object(key="Child", location=room, home=room)
        matches = self.panel.related(
            _worker(), model="objects.objectdb", field="db_location", search="Search"
        )
        self.assertEqual(matches["rows"][0]["id"], room.pk)
        graph = self.panel.relations(_worker(), model="objects.objectdb", pk=child.pk)
        self.assertIn(
            room.pk,
            [row["id"] for row in graph["forward"] if row["field"] == "db_location"],
        )

    def test_relation_search_includes_aliases_and_tags(self):
        from evennia.utils.create import create_object

        room = create_object(key="Unrelated key", nohome=True)
        room.aliases.add("Searchable alias")
        room.tags.add("searchable-tag")
        aliases = self.panel.related(
            _worker(), model="objects.objectdb", field="db_location", search="Searchable alias"
        )
        tags = self.panel.related(
            _worker(), model="objects.objectdb", field="db_location", search="searchable-tag"
        )
        self.assertEqual(aliases["rows"][0]["id"], room.pk)
        self.assertEqual(tags["rows"][0]["id"], room.pk)

    def test_relations_and_tags_round_trip_through_the_records_form(self):
        from django.contrib.auth.models import Group

        target = type(self.actor).objects.create(username="related-target", is_active=True)
        group = Group.objects.create(name="Console operators")
        result = self.panel.save(
            self.io(),
            model="accounts.accountdb",
            pk=target.pk,
            values={"first_name": "Related"},
            relations={"groups": [group.pk], "user_permissions": []},
            tags=[{"key": "reviewed", "category": "console", "type": None, "data": "yes"}],
        )
        self.assertEqual(result["status"], "changed")
        self.assertEqual(list(target.groups.values_list("pk", flat=True)), [group.pk])
        extras = self.panel.extras(_worker(), model="accounts.accountdb", pk=target.pk)
        self.assertEqual(extras["relations"]["groups"], [group.pk])
        self.assertIn(
            {"key": "reviewed", "category": "console", "type": None, "data": "yes"},
            extras["tags"],
        )


class TestSaveAudit(ActingTestCase):
    """Every write is recorded, with what it changed."""

    def test_a_create_is_audited(self):
        self.panel.save(
            self.io(),
            model="help.helpentry",
            values={"db_key": "audited", "db_entrytext": "b", "db_help_category": "general"},
        )
        row = ConsoleAuditEvent.objects.get(panel="records", operation="add")
        self.assertEqual(row.outcome, ConsoleAuditEvent.OUTCOME_SUCCESS)
        self.assertIn("db_key", row.after)
        self.assertEqual(row.before, {})

    def test_a_change_records_the_previous_values(self):
        entry = HelpEntry.objects.create(db_key="k", db_entrytext="was", db_help_category="general")
        self.panel.save(
            self.io(), model="help.helpentry", pk=entry.pk, values={"db_entrytext": "now"}
        )
        row = ConsoleAuditEvent.objects.get(panel="records", operation="change")
        self.assertEqual(row.before["db_entrytext"], "was")
        self.assertEqual(row.after["db_entrytext"], "now")

    def test_a_failed_write_is_audited_with_its_outcome(self):
        # The five-way taxonomy reaches the audit trail, so "wrote, then
        # faulted" is distinguishable afterwards from "rejected before any
        # write".
        with patch(
            "evennia.console.panels.records.mutate_admin",
            return_value=AdminMutationResult(status="partial", object_id=7, message="broke"),
        ):
            self.panel.save(self.io(), model="help.helpentry", values={"db_key": "x"})
        row = ConsoleAuditEvent.objects.get(panel="records", operation="add")
        self.assertEqual(row.outcome, ConsoleAuditEvent.OUTCOME_PARTIAL)
        self.assertFalse(row.is_retryable)

    def test_a_conflict_is_recorded_as_retryable(self):
        with patch(
            "evennia.console.panels.records.mutate_admin",
            return_value=AdminMutationResult(status="conflict", message="taken"),
        ):
            self.panel.save(self.io(), model="help.helpentry", values={"db_key": "x"})
        row = ConsoleAuditEvent.objects.get(panel="records", operation="add")
        self.assertEqual(row.outcome, ConsoleAuditEvent.OUTCOME_CONFLICT)
        self.assertTrue(row.is_retryable)
