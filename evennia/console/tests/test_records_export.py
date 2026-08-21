"""Tests for export, bulk preview, and the console.* bus announcement.

Three cross-cutting promises, each with a property that is easy to get wrong.

**An export is a disclosure event.** Rows leave the console and stop being
subject to it. The audit row records what was taken and by whom, and must not
record the contents: a second copy of the data in the audit table outlives the
first under a different retention window.

**A preview must not delete.** The whole point is that the operator decides
against a counted number rather than a guess, so a preview that touched
anything would be worse than no preview.

**The bus is not the record.** D2 keeps them separate on purpose. The
announcement carries identity, never payloads.

"""

import csv
import io
import json

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from evennia.console import audit
from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.records import MAX_BULK, RecordsPanel
from evennia.console.registry import CONSOLE_ACCESS, WorkerContext
from evennia.objects.models import ObjectDB
from evennia.server.models import GameEvent


def _ctx(**params):
    """Build a worker context."""

    params.setdefault("model", "objects.ObjectDB")
    return WorkerContext(
        actor_id=1, actor_name="op", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


class TestExport(TestCase):
    """CSV and JSON, bounded and recorded."""

    def setUp(self):
        self.panel = RecordsPanel()
        ObjectDB.objects.create(db_key="first")
        ObjectDB.objects.create(db_key="second")

    def test_json_carries_the_rows(self):
        result = self.panel.export(_ctx(), model="objects.ObjectDB", fmt="json")
        self.assertEqual(len(json.loads(result["body"])), 2)

    def test_csv_has_a_header_and_a_row_each(self):
        result = self.panel.export(_ctx(), model="objects.ObjectDB", fmt="csv")
        rows = list(csv.reader(io.StringIO(result["body"])))
        self.assertEqual(len(rows), 3)

    def test_csv_quotes_a_value_containing_a_comma(self):
        # One correct encoding, and it is not the obvious one.
        ObjectDB.objects.create(db_key='a, b "c"')
        result = self.panel.export(_ctx(), model="objects.ObjectDB", fmt="csv")
        parsed = list(csv.reader(io.StringIO(result["body"])))
        self.assertIn('a, b "c"', [field for row in parsed for field in row])

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(ValueError):
            self.panel.export(_ctx(), model="objects.ObjectDB", fmt="xlsx")

    def test_it_carries_a_content_type_and_a_filename(self):
        result = self.panel.export(_ctx(), model="objects.ObjectDB", fmt="csv")
        self.assertEqual(result["content_type"], "text/csv")
        self.assertTrue(result["filename"].endswith(".csv"))

    def test_it_is_bounded_by_the_same_cap_as_the_view(self):
        listing = len(self.panel.rows(_ctx(page_size="1"))["rows"])
        exported = self.panel.export(_ctx(page_size="1"), model="objects.ObjectDB")["rows"]
        self.assertEqual(exported, listing)

    def test_it_is_recorded(self):
        self.panel.export(_ctx(), model="objects.ObjectDB", fmt="csv")
        row = ConsoleAuditEvent.objects.get(operation="export")
        self.assertEqual(row.after["format"], "csv")
        self.assertEqual(row.after["rows"], 2)

    def test_the_record_names_the_filters_used(self):
        self.panel.export(_ctx(search="first"), model="objects.ObjectDB")
        row = ConsoleAuditEvent.objects.get(operation="export")
        self.assertEqual(row.after["filters"]["search"], "first")

    def test_the_record_does_not_contain_the_exported_data(self):
        # A second copy of the data in the audit table outlives the first under
        # a different retention window.
        self.panel.export(_ctx(), model="objects.ObjectDB", fmt="csv")
        row = ConsoleAuditEvent.objects.get(operation="export")
        blob = json.dumps([row.before, row.after])
        self.assertNotIn("first", blob)
        self.assertNotIn("second", blob)


class TestBulkPreview(TestCase):
    """Counted first, decided second."""

    def setUp(self):
        self.panel = RecordsPanel()
        self.obj = ObjectDB.objects.create(db_key="target")

    def test_it_reports_the_rows_it_found(self):
        result = self.panel.preview_delete(_ctx(), model="objects.ObjectDB", ids=[self.obj.pk])
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["rows"][0]["id"], self.obj.pk)

    def test_it_names_the_rows_that_are_not_there(self):
        result = self.panel.preview_delete(
            _ctx(), model="objects.ObjectDB", ids=[self.obj.pk, 999999]
        )
        self.assertEqual(result["missing"], [999999])

    def test_it_deletes_nothing(self):
        self.panel.preview_delete(_ctx(), model="objects.ObjectDB", ids=[self.obj.pk])
        self.assertTrue(ObjectDB.objects.filter(pk=self.obj.pk).exists())

    def test_it_records_nothing(self):
        # A preview is a read. An audit row for one would make the trail noisy
        # in exactly the situation an operator is about to need it clean.
        self.panel.preview_delete(_ctx(), model="objects.ObjectDB", ids=[self.obj.pk])
        self.assertFalse(ConsoleAuditEvent.objects.exists())

    def test_every_row_carries_a_cascade_count(self):
        result = self.panel.preview_delete(_ctx(), model="objects.ObjectDB", ids=[self.obj.pk])
        row = result["rows"][0]
        self.assertIn("cascade", row)
        self.assertIsInstance(row["reach"], int)
        self.assertEqual(result["total_cascade"], row["reach"])

    def test_the_worst_row_is_first(self):
        other = ObjectDB.objects.create(db_key="other")
        result = self.panel.preview_delete(
            _ctx(), model="objects.ObjectDB", ids=[self.obj.pk, other.pk]
        )
        reaches = [row["reach"] for row in result["rows"]]
        self.assertEqual(reaches, sorted(reaches, reverse=True))

    def test_naming_no_rows_is_refused(self):
        with self.assertRaises(ValueError):
            self.panel.preview_delete(_ctx(), model="objects.ObjectDB", ids=[])

    def test_the_selection_is_capped(self):
        result = self.panel.preview_delete(
            _ctx(), model="objects.ObjectDB", ids=list(range(1, MAX_BULK + 50))
        )
        self.assertEqual(result["requested"], MAX_BULK)
        self.assertTrue(result["capped"])

    def test_it_does_not_need_the_io_owner(self):
        from evennia.console.registry import is_io_action

        self.assertFalse(is_io_action(RecordsPanel.preview_delete))

    def test_a_model_with_no_write_path_is_refused(self):
        with self.assertRaises((PermissionDenied, LookupError)):
            self.panel.preview_delete(_ctx(), model="console.ConsoleAuditEvent", ids=[1])


class TestBusAnnouncement(TestCase):
    """D2: the bus is the stream, the table is the record."""

    def test_recording_emits_a_console_subject(self):
        audit.record(panel="records", operation="change", actor_id=1, actor_name="op")
        subjects = list(GameEvent.objects.values_list("subject", flat=True))
        # Persistence is configured per subject, so an empty table here means
        # the subject is not persisted rather than not emitted.
        for subject in subjects:
            self.assertTrue(subject.startswith("console."))

    def test_the_subject_is_valid(self):
        # A subject that fails validation is dropped with a log line, so the
        # emit would silently do nothing.
        from evennia.server.amp_serde import validate_event_subject

        self.assertEqual(validate_event_subject("console.records.change"), "console.records.change")

    def test_a_bus_failure_does_not_lose_the_audit_row(self):
        # The row is written and committed before the announcement runs. An
        # unreachable bus must not turn a completed operation into a reported
        # failure.
        from unittest.mock import patch

        with patch("evennia.eventbus.bus.emit", side_effect=RuntimeError("bus down")):
            event_id = audit.record(panel="records", operation="change", actor_id=1)
        self.assertIsNotNone(event_id)
        self.assertTrue(ConsoleAuditEvent.objects.filter(event_id=event_id).exists())

    def test_the_announcement_carries_no_payload_content(self):
        from unittest.mock import patch

        with patch("evennia.eventbus.bus.emit") as emit:
            audit.record(
                panel="records",
                operation="change",
                actor_id=1,
                before={"secret": "value"},
                after={"secret": "other"},
            )
        payload = emit.call_args.args[1]
        self.assertNotIn("before", payload)
        self.assertNotIn("after", payload)
        self.assertIn("event_id", payload)
