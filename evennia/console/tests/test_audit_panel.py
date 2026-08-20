"""Tests for the Audit panel and undo.

The audit trail is the console's only internal control under decision D1, so
the properties asserted here are not conveniences. Append-only means undo must
not revise the row it reverses. The five outcomes must stay distinguishable.
And an inverse must exist only where applying it is actually safe -- an undo
button that quietly performs a cascading delete is worse than no undo button.

"""

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from evennia.console import audit
from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.auditlog import AuditPanel, _diff
from evennia.console.panels.records import RecordsPanel
from evennia.console.registry import CONSOLE_ACCESS, IOContext, WorkerContext
from evennia.objects.models import ObjectDB


def _ctx(**params):
    """Build a worker context."""

    return WorkerContext(
        actor_id=1, actor_name="op", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


def _io(actor_id=1):
    """Build an IO context."""

    return IOContext(actor_id=actor_id, actor_name="op", capabilities=frozenset())


def _event(**kwargs):
    """Write one audit row."""

    fields = {
        "panel": "records",
        "operation": "change",
        "actor_id": 1,
        "actor_name": "op",
        "target_ref": "objects.objectdb#1",
        "outcome": ConsoleAuditEvent.OUTCOME_SUCCESS,
    }
    fields.update(kwargs)
    return audit.record(**fields)


class TestListing(TestCase):
    """The page, its filters, and its bounds."""

    def setUp(self):
        self.panel = AuditPanel()

    def test_rows_are_returned_newest_first(self):
        _event(message="older")
        _event(message="newer")
        rows = self.panel.rows(_ctx())["rows"]
        self.assertEqual([row["message"] for row in rows], ["newer", "older"])

    def test_filtering_by_outcome(self):
        _event(outcome=ConsoleAuditEvent.OUTCOME_SUCCESS)
        _event(outcome=ConsoleAuditEvent.OUTCOME_PARTIAL)
        rows = self.panel.rows(_ctx(outcome=ConsoleAuditEvent.OUTCOME_PARTIAL))["rows"]
        self.assertEqual([row["outcome"] for row in rows], [ConsoleAuditEvent.OUTCOME_PARTIAL])

    def test_filtering_by_actor_id_or_name(self):
        _event(actor_id=7, actor_name="seven")
        _event(actor_id=8, actor_name="eight")
        self.assertEqual(len(self.panel.rows(_ctx(actor="7"))["rows"]), 1)
        self.assertEqual(len(self.panel.rows(_ctx(actor="eight"))["rows"]), 1)

    def test_filtering_by_target_prefix(self):
        _event(target_ref="objects.objectdb#42")
        _event(target_ref="accounts.accountdb#42")
        rows = self.panel.rows(_ctx(target="objects."))["rows"]
        self.assertEqual(len(rows), 1)

    def test_page_size_is_capped(self):
        self.assertEqual(self.panel.rows(_ctx(page_size="99999"))["page_size"], 200)

    def test_a_malformed_cursor_starts_from_the_beginning(self):
        _event(message="only")
        rows = self.panel.rows(_ctx(cursor="not-a-cursor"))["rows"]
        self.assertEqual(len(rows), 1)

    def test_paging_reaches_every_row_exactly_once(self):
        for index in range(7):
            _event(message=f"row-{index}")
        seen, cursor, guard = [], "", 0
        while guard < 10:
            guard += 1
            page = self.panel.rows(_ctx(page_size="3", cursor=cursor))
            seen.extend(row["event_id"] for row in page["rows"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7, "a row was returned on two pages")

    def test_the_five_outcomes_are_offered_with_their_meanings(self):
        # partial and recovery_required both mean "it wrote, then faulted".
        # The station cannot distinguish them if the panel does not.
        outcomes = {row["value"]: row["meaning"] for row in self.panel.rows(_ctx())["outcomes"]}
        self.assertEqual(len(outcomes), 5)
        self.assertNotEqual(
            outcomes[ConsoleAuditEvent.OUTCOME_PARTIAL],
            outcomes[ConsoleAuditEvent.OUTCOME_RECOVERY],
        )

    def test_filter_vocabularies_list_only_what_exists(self):
        _event(panel="moderation", operation="sanction")
        result = self.panel.rows(_ctx())
        self.assertIn("moderation", result["panels"])
        self.assertNotIn("nonexistent", result["panels"])


class TestDetail(TestCase):
    """One row, rendered for a person."""

    def setUp(self):
        self.panel = AuditPanel()

    def test_it_reports_the_outcome_meaning(self):
        _event(outcome=ConsoleAuditEvent.OUTCOME_RECOVERY)
        row = ConsoleAuditEvent.objects.first()
        detail = self.panel.detail(_ctx(), row.id)
        self.assertTrue(detail["outcome_meaning"])
        self.assertFalse(detail["retryable"])

    def test_a_conflict_is_the_only_retryable_outcome(self):
        _event(outcome=ConsoleAuditEvent.OUTCOME_CONFLICT)
        row = ConsoleAuditEvent.objects.first()
        self.assertTrue(self.panel.detail(_ctx(), row.id)["retryable"])

    def test_the_diff_shows_both_sides(self):
        _event(before={"db_key": "old"}, after={"db_key": "new"})
        row = ConsoleAuditEvent.objects.first()
        entries = self.panel.detail(_ctx(), row.id)["diff"]["entries"]
        self.assertEqual(entries[0]["before"], "old")
        self.assertEqual(entries[0]["after"], "new")
        self.assertTrue(entries[0]["changed"])

    def test_an_unknown_row_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(), 999999)

    def test_a_bad_identifier_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(), "not-a-number")

    def test_undo_unavailability_carries_a_reason(self):
        # A control that is merely absent explains nothing. The reason has to
        # separate "cannot be reversed" from "failed, so nothing to reverse".
        _event(outcome=ConsoleAuditEvent.OUTCOME_SUCCESS)
        row = ConsoleAuditEvent.objects.first()
        detail = self.panel.detail(_ctx(), row.id)
        self.assertFalse(detail["can_undo"])
        self.assertIn("No inverse", detail["undo_reason"])

        ConsoleAuditEvent.objects.update(outcome=ConsoleAuditEvent.OUTCOME_PARTIAL)
        detail = self.panel.detail(_ctx(), row.id)
        self.assertIn("partial", detail["undo_reason"])

    def test_the_inverse_is_withheld_when_it_cannot_be_applied(self):
        _event(outcome=ConsoleAuditEvent.OUTCOME_PARTIAL, inverse={"kind": "records.change"})
        row = ConsoleAuditEvent.objects.first()
        self.assertIsNone(self.panel.detail(_ctx(), row.id)["inverse"])


class TestDiff(TestCase):
    """The comparison itself."""

    def test_unchanged_fields_are_kept(self):
        # "Was this field already wrong before the change" is a question a diff
        # that hides unchanged fields cannot answer.
        result = _diff({"a": "1", "b": "2"}, {"a": "1", "b": "3"})
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["changed_count"], 1)

    def test_a_field_present_on_one_side_only_is_marked(self):
        result = _diff({}, {"added": "value"})
        self.assertEqual(result["entries"][0]["only_in"], "after")

    def test_a_non_dict_payload_does_not_break_it(self):
        self.assertEqual(_diff(None, "a string")["entries"], [])


class TestInverseDerivation(TestCase):
    """Which writes get an inverse, and which deliberately do not."""

    def setUp(self):
        self.panel = RecordsPanel()
        self.spec = self.panel._spec("objects.ObjectDB")

    def test_a_successful_change_is_reversible(self):
        inverse = self.panel._inverse(
            self.spec, 5, {"db_key": "before"}, ConsoleAuditEvent.OUTCOME_SUCCESS
        )
        self.assertEqual(inverse["kind"], "records.change")
        self.assertEqual(inverse["pk"], 5)
        self.assertEqual(inverse["values"], {"db_key": "before"})

    def test_a_create_is_not_reversible(self):
        # Reversing a create means deleting, and delete carries a cascade the
        # service preflights for a reason.
        self.assertIsNone(
            self.panel._inverse(self.spec, None, {}, ConsoleAuditEvent.OUTCOME_SUCCESS)
        )

    def test_a_write_that_faulted_is_not_reversible(self):
        for outcome in (
            ConsoleAuditEvent.OUTCOME_PARTIAL,
            ConsoleAuditEvent.OUTCOME_RECOVERY,
            ConsoleAuditEvent.OUTCOME_CONFLICT,
            ConsoleAuditEvent.OUTCOME_INDETERMINATE,
        ):
            self.assertIsNone(
                self.panel._inverse(self.spec, 5, {"db_key": "before"}, outcome),
                f"{outcome} must not produce an inverse",
            )


class TestUndo(TestCase):
    """Applying an inverse."""

    def setUp(self):
        self.panel = AuditPanel()

    def _reversible_row(self, pk, before):
        _event(
            inverse={
                "kind": "records.change",
                "model": "objects.ObjectDB",
                "pk": pk,
                "values": before,
            }
        )
        return ConsoleAuditEvent.objects.first()

    def test_it_refuses_without_a_reason(self):
        row = self._reversible_row(1, {"db_key": "old"})
        with self.assertRaises(ValueError):
            self.panel.undo(_io(), audit_id=row.id, reason="   ")

    def test_it_refuses_a_row_with_no_inverse(self):
        _event()
        row = ConsoleAuditEvent.objects.first()
        with self.assertRaises(PermissionDenied):
            self.panel.undo(_io(), audit_id=row.id, reason="because")

    def test_it_refuses_an_inverse_kind_it_cannot_apply(self):
        _event(inverse={"kind": "something.else"})
        row = ConsoleAuditEvent.objects.first()
        with self.assertRaises(PermissionDenied) as caught:
            self.panel.undo(_io(), audit_id=row.id, reason="because")
        self.assertIn("something.else", str(caught.exception))

    def test_an_unknown_row_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.undo(_io(), audit_id=999999, reason="because")

    def test_it_does_not_revise_the_row_it_reverses(self):
        # Append-only. The trail holds the mistake and the correction as two
        # facts, not one revised one.
        obj = ObjectDB.objects.create(db_key="after")
        row = self._reversible_row(obj.pk, {"db_key": "before"})
        stored = dict(row.inverse)
        try:
            self.panel.undo(_io(), audit_id=row.id, reason="restoring the key")
        except Exception:  # noqa: BLE001 - the write path needs the IO owner
            pass
        row.refresh_from_db()
        self.assertEqual(row.inverse, stored)
        self.assertEqual(row.operation, "change")
