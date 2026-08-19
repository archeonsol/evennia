"""Tests for the console audit trail.

The behaviour that matters is what happens when things go wrong. An audit
write is the *last* step of an operation that has already mutated game state,
so it must never raise, never roll anything back, and never lose the row
because a payload was awkward.

"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from evennia.console import audit
from evennia.console.models import ConsoleAuditEvent


class TestRecord(TestCase):
    """Rows are written, bounded, and classified."""

    def test_writes_a_row_and_returns_its_id(self):
        event_id = audit.record(panel="records", operation="change", actor_id=7, actor_name="staff")
        self.assertTrue(event_id)
        row = ConsoleAuditEvent.objects.get(event_id=event_id)
        self.assertEqual(row.panel, "records")
        self.assertEqual(row.operation, "change")
        self.assertEqual(row.actor_id, 7)
        self.assertEqual(row.actor_name, "staff")

    def test_defaults_to_success(self):
        event_id = audit.record(panel="records", operation="view")
        row = ConsoleAuditEvent.objects.get(event_id=event_id)
        self.assertEqual(row.outcome, ConsoleAuditEvent.OUTCOME_SUCCESS)

    def test_payloads_are_stored_as_plain_data(self):
        event_id = audit.record(
            panel="records",
            operation="change",
            before={"key": "old"},
            after={"key": "new"},
        )
        row = ConsoleAuditEvent.objects.get(event_id=event_id)
        self.assertEqual(row.before, {"key": "old"})
        self.assertEqual(row.after, {"key": "new"})

    def test_a_live_object_in_a_payload_does_not_lose_the_row(self):
        # An audit row is written after the mutation already happened. A payload
        # the codec refuses must degrade to a note, never to a missing record.
        event_id = audit.record(panel="records", operation="change", before=ConsoleAuditEvent)
        row = ConsoleAuditEvent.objects.get(event_id=event_id)
        self.assertIn("_unrecordable", row.before)

    def test_never_raises_when_the_write_fails(self):
        with override_settings():
            original = ConsoleAuditEvent.objects.create

            def _boom(*args, **kwargs):
                raise RuntimeError("database is on fire")

            ConsoleAuditEvent.objects.create = _boom
            try:
                result = audit.record(panel="records", operation="change")
            finally:
                ConsoleAuditEvent.objects.create = original
        self.assertIsNone(result)

    def test_overlong_text_is_truncated_not_rejected(self):
        event_id = audit.record(
            panel="records",
            operation="change",
            actor_name="n" * 500,
            message="m" * 900,
            target_ref="t" * 400,
        )
        row = ConsoleAuditEvent.objects.get(event_id=event_id)
        self.assertEqual(len(row.actor_name), 255)
        self.assertEqual(len(row.message), 500)
        self.assertEqual(len(row.target_ref), 160)


class TestOutcomes(TestCase):
    """Only a pre-write rejection is retryable."""

    def test_conflict_is_the_only_retryable_outcome(self):
        for outcome in (
            ConsoleAuditEvent.OUTCOME_CONFLICT,
            ConsoleAuditEvent.OUTCOME_SUCCESS,
            ConsoleAuditEvent.OUTCOME_PARTIAL,
            ConsoleAuditEvent.OUTCOME_RECOVERY,
            ConsoleAuditEvent.OUTCOME_INDETERMINATE,
        ):
            row = ConsoleAuditEvent(outcome=outcome)
            self.assertEqual(
                row.is_retryable,
                outcome == ConsoleAuditEvent.OUTCOME_CONFLICT,
                f"{outcome} retryability is wrong",
            )

    def test_every_post_write_outcome_is_non_retryable(self):
        self.assertEqual(
            ConsoleAuditEvent.NON_RETRYABLE,
            {
                ConsoleAuditEvent.OUTCOME_PARTIAL,
                ConsoleAuditEvent.OUTCOME_RECOVERY,
                ConsoleAuditEvent.OUTCOME_INDETERMINATE,
            },
        )

    def test_undo_needs_a_successful_outcome_and_an_inverse(self):
        self.assertFalse(ConsoleAuditEvent(outcome="success", inverse=None).can_undo)
        self.assertFalse(ConsoleAuditEvent(outcome="partial", inverse={"a": 1}).can_undo)
        self.assertTrue(ConsoleAuditEvent(outcome="success", inverse={"a": 1}).can_undo)


class TestRetention(TestCase):
    """Classification, and the two classes that never prune."""

    def test_moderation_and_authorization_are_permanent(self):
        self.assertEqual(audit.retention_for("moderation", "sanction"), "permanent")
        self.assertEqual(audit.retention_for("authorization", "grant"), "permanent")

    def test_break_glass_is_permanent_from_any_panel(self):
        self.assertEqual(audit.retention_for("records", "break_glass_issue"), "permanent")

    def test_repl_and_sql_prune_on_their_own_window(self):
        self.assertEqual(audit.retention_for("repl", "execute"), "repl")
        self.assertEqual(audit.retention_for("sql", "query"), "repl")

    def test_everything_else_is_normal(self):
        self.assertEqual(audit.retention_for("records", "change"), "normal")

    @override_settings(CONSOLE_AUDIT_RETENTION_DAYS=30, CONSOLE_AUDIT_REPL_RETENTION_DAYS=7)
    def test_prune_respects_each_window(self):
        now = timezone.now()
        old = now - timedelta(days=60)
        recent = now - timedelta(days=10)
        for retention, created in (
            ("normal", old),
            ("normal", recent),
            ("repl", recent),
            ("permanent", old),
        ):
            ConsoleAuditEvent.objects.create(
                event_id=audit.new_event_id(),
                panel="p",
                operation="o",
                retention=retention,
                created_at=created,
            )
        deleted = audit.prune(now=now)
        self.assertEqual(deleted["normal"], 1)
        self.assertEqual(deleted["repl"], 1)
        self.assertEqual(ConsoleAuditEvent.objects.filter(retention="permanent").count(), 1)
        self.assertEqual(ConsoleAuditEvent.objects.filter(retention="normal").count(), 1)

    @override_settings(CONSOLE_AUDIT_RETENTION_DAYS=30)
    def test_permanent_rows_survive_an_arbitrarily_long_wait(self):
        ConsoleAuditEvent.objects.create(
            event_id=audit.new_event_id(),
            panel="moderation",
            operation="sanction_issue",
            retention="permanent",
            created_at=timezone.now() - timedelta(days=4000),
        )
        audit.prune()
        self.assertEqual(ConsoleAuditEvent.objects.count(), 1)

    @override_settings(CONSOLE_AUDIT_RETENTION_DAYS=0)
    def test_zero_disables_pruning(self):
        ConsoleAuditEvent.objects.create(
            event_id=audit.new_event_id(),
            panel="records",
            operation="change",
            retention="normal",
            created_at=timezone.now() - timedelta(days=4000),
        )
        deleted = audit.prune()
        self.assertEqual(deleted["normal"], 0)
        self.assertEqual(ConsoleAuditEvent.objects.count(), 1)
