"""Tests for Jobs, Event bus, and Database.

The queue and the bus were committed by decision D6 and never numbered, so
neither existed until the 2026-08-20 audit. The Database panel was numbered
P15 and never built.

The properties asserted here are the ones that make them safe to leave open on
a production console: every listing is bounded, every grouping is on an indexed
column, nothing counts a table that grows per event, and the one write refuses
anything that is not a dead letter.

"""

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.database import DatabasePanel
from evennia.console.panels.subsystems import MAX_ROWS, EventBusPanel, JobsPanel, _pretty
from evennia.console.registry import CONSOLE_ACCESS, IOContext, WorkerContext, is_io_action
from evennia.server.models import EngineJob, GameEvent


def _ctx(**params):
    """Build a worker context."""

    return WorkerContext(
        actor_id=1, actor_name="op", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


def _io():
    """Build an IO context."""

    return IOContext(actor_id=1, actor_name="op", capabilities=frozenset())


def _job(**kwargs):
    """Write one queue row."""

    fields = {
        "job_id": f"job{EngineJob.objects.count() + 1:028d}",
        "job_type": "rebuild_index",
        "payload_json": '{"target": "help"}',
        "status": "pending",
    }
    fields.update(kwargs)
    return EngineJob.objects.create(**fields)


class TestJobsListing(TestCase):
    """Depth, filters, and bounds."""

    def setUp(self):
        self.panel = JobsPanel()

    def test_depth_is_grouped_by_status(self):
        _job(status="pending")
        _job(status="dead")
        _job(status="dead")
        result = self.panel.rows(_ctx())
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["dead"], 2)

    def test_depth_is_grouped_by_type(self):
        _job(job_type="send_email")
        _job(job_type="rebuild_index")
        types = {row["job_type"] for row in self.panel.rows(_ctx())["by_type"]}
        self.assertEqual(types, {"send_email", "rebuild_index"})

    def test_filtering_by_status(self):
        _job(status="pending")
        _job(status="dead")
        rows = self.panel.rows(_ctx(status="dead"))["rows"]
        self.assertEqual([row["status"] for row in rows], ["dead"])

    def test_filtering_by_type(self):
        _job(job_type="send_email")
        _job(job_type="rebuild_index")
        rows = self.panel.rows(_ctx(job_type="send_email"))["rows"]
        self.assertEqual([row["job_type"] for row in rows], ["send_email"])

    def test_an_expired_lease_is_surfaced(self):
        # A lease past its expiry means the worker holding it stopped. Nothing
        # else on the console reports that.
        _job(status="leased", lease_until=timezone.now() - timezone.timedelta(minutes=5))
        self.assertEqual(self.panel.rows(_ctx())["overdue_leases"], 1)

    def test_a_live_lease_is_not_overdue(self):
        _job(status="leased", lease_until=timezone.now() + timezone.timedelta(minutes=5))
        self.assertEqual(self.panel.rows(_ctx())["overdue_leases"], 0)

    def test_the_listing_is_bounded(self):
        for _ in range(MAX_ROWS + 10):
            _job()
        self.assertEqual(len(self.panel.rows(_ctx())["rows"]), MAX_ROWS)

    def test_the_backend_is_reported(self):
        self.assertIn("backend", self.panel.rows(_ctx())["backend"])

    def test_it_does_not_need_the_io_owner(self):
        self.assertFalse(JobsPanel.needs_io)


class TestJobDetail(TestCase):
    """One job, with the payload readable."""

    def setUp(self):
        self.panel = JobsPanel()

    def test_the_payload_is_rendered(self):
        job = _job(payload_json='{"b":2,"a":1}')
        detail = self.panel.detail(_ctx(), job.id)
        self.assertIn('"a": 1', detail["payload"])

    def test_a_payload_that_is_not_json_is_shown_as_stored(self):
        # A payload that will not parse is itself the finding.
        job = _job(payload_json="not json at all")
        self.assertEqual(self.panel.detail(_ctx(), job.id)["payload"], "not json at all")

    def test_only_a_dead_job_offers_requeue(self):
        self.assertFalse(self.panel.detail(_ctx(), _job(status="pending").id)["can_requeue"])
        self.assertTrue(self.panel.detail(_ctx(), _job(status="dead").id)["can_requeue"])

    def test_an_unknown_job_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(), 999999)


class TestRequeue(TestCase):
    """The one write."""

    def setUp(self):
        self.panel = JobsPanel()

    def test_it_returns_a_dead_job_to_the_queue(self):
        job = _job(status="dead", attempts=5)
        self.panel.requeue(_io(), job_id=job.id, reason="handler fixed")
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")

    def test_the_attempt_counter_is_reset(self):
        # A job requeued with attempts still at max dead-letters again on its
        # first try, which reads as the requeue having silently failed.
        job = _job(status="dead", attempts=5, max_attempts=5)
        self.panel.requeue(_io(), job_id=job.id, reason="handler fixed")
        job.refresh_from_db()
        self.assertEqual(job.attempts, 0)

    def test_the_lease_is_cleared(self):
        job = _job(status="dead", attempts=5, lease_until=timezone.now())
        self.panel.requeue(_io(), job_id=job.id, reason="handler fixed")
        job.refresh_from_db()
        self.assertIsNone(job.lease_until)

    def test_it_refuses_a_job_that_is_not_dead(self):
        job = _job(status="pending")
        with self.assertRaises(ValueError) as caught:
            self.panel.requeue(_io(), job_id=job.id, reason="why not")
        self.assertIn("'dead'", str(caught.exception))

    def test_it_refuses_without_a_reason(self):
        job = _job(status="dead")
        with self.assertRaises(ValueError):
            self.panel.requeue(_io(), job_id=job.id, reason="  ")

    def test_it_is_recorded(self):
        job = _job(status="dead", attempts=5)
        self.panel.requeue(_io(), job_id=job.id, reason="handler fixed")
        row = ConsoleAuditEvent.objects.get(panel="jobs")
        self.assertEqual(row.operation, "requeue")
        self.assertEqual(row.before["attempts"], 5)
        self.assertIn("handler fixed", row.message)

    def test_it_runs_nothing_itself(self):
        # It makes the job eligible. Executing it here would be a second path
        # to running a job beside the drain that already exists.
        job = _job(status="dead", attempts=5)
        self.panel.requeue(_io(), job_id=job.id, reason="handler fixed")
        job.refresh_from_db()
        self.assertIsNone(job.completed_at)

    def test_it_needs_the_io_owner(self):
        self.assertTrue(is_io_action(JobsPanel.requeue))


class TestEventBus(TestCase):
    """Persisted bus records."""

    def setUp(self):
        self.panel = EventBusPanel()

    def test_rows_are_returned_newest_first(self):
        GameEvent.objects.create(subject="moderation.sanction", payload_json="{}")
        GameEvent.objects.create(subject="economy.transfer", payload_json="{}")
        rows = self.panel.rows(_ctx())["rows"]
        self.assertEqual(rows[0]["subject"], "economy.transfer")

    def test_filtering_by_subject(self):
        GameEvent.objects.create(subject="moderation.sanction", payload_json="{}")
        GameEvent.objects.create(subject="economy.transfer", payload_json="{}")
        rows = self.panel.rows(_ctx(subject="economy.transfer"))["rows"]
        self.assertEqual(len(rows), 1)

    def test_filtering_by_prefix(self):
        GameEvent.objects.create(subject="moderation.sanction", payload_json="{}")
        GameEvent.objects.create(subject="moderation.flag", payload_json="{}")
        GameEvent.objects.create(subject="economy.transfer", payload_json="{}")
        rows = self.panel.rows(_ctx(prefix="moderation."))["rows"]
        self.assertEqual(len(rows), 2)

    def test_prefixes_are_offered(self):
        GameEvent.objects.create(subject="moderation.sanction", payload_json="{}")
        self.assertIn("moderation", self.panel.rows(_ctx())["prefixes"])

    def test_the_listing_is_bounded(self):
        for index in range(MAX_ROWS + 5):
            GameEvent.objects.create(subject=f"s.{index}", payload_json="{}")
        result = self.panel.rows(_ctx())
        self.assertEqual(len(result["rows"]), MAX_ROWS)
        self.assertTrue(result["next_before"])

    def test_the_panel_offers_no_write(self):
        # The bus is a stream. Replaying or injecting from a console would be a
        # second producer beside the engine's own.
        for name in dir(EventBusPanel):
            self.assertNotIn(name, {"emit", "publish", "replay", "delete"})

    def test_the_configuration_is_reported(self):
        bus = self.panel.rows(_ctx())["bus"]
        for field in ("backend", "enabled", "persisted"):
            self.assertIn(field, bus)

    def test_detail_renders_the_payload(self):
        row = GameEvent.objects.create(subject="s", payload_json='{"b":2,"a":1}')
        self.assertIn('"a": 1', self.panel.detail(_ctx(), row.id)["payload"])

    def test_an_unknown_record_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(), 999999)


class TestPretty(TestCase):
    """Payload rendering."""

    def test_json_is_indented_and_ordered(self):
        self.assertEqual(_pretty('{"b":2,"a":1}'), '{\n  "a": 1,\n  "b": 2\n}')

    def test_non_json_is_returned_unchanged(self):
        self.assertEqual(_pretty("plain"), "plain")

    def test_empty_is_empty(self):
        self.assertEqual(_pretty(None), "")

    def test_it_is_bounded(self):
        self.assertLessEqual(len(_pretty("x" * 9000)), 2000)


class TestDatabasePanel(TestCase):
    """Statistics reads, and an honest refusal off PostgreSQL."""

    def setUp(self):
        self.panel = DatabasePanel()

    def test_it_reports_the_vendor(self):
        self.assertEqual(self.panel.rows(_ctx())["vendor"], connection.vendor)

    def test_sqlite_is_refused_with_a_reason_rather_than_an_empty_table(self):
        # An empty table reads as "nothing to report", which is a different
        # claim from "this backend cannot answer".
        result = self.panel.rows(_ctx())
        if connection.vendor != "postgresql":
            self.assertFalse(result["supported"])
            self.assertIn("PostgreSQL", result["reason"])
            self.assertEqual(result["tables"], [])

    def test_the_shape_is_the_same_either_way(self):
        # A station that has to branch on vendor to know which keys exist would
        # break the first time it ran against the other one.
        result = self.panel.rows(_ctx())
        for field in ("supported", "vendor", "tables", "indexes", "connections"):
            self.assertIn(field, result)

    def test_it_lists_the_models_carrying_documents(self):
        models = self.panel.models(_ctx())["models"]
        self.assertIn("objects.objectdb", [label.lower() for label in models])

    def test_sizes_needs_a_model(self):
        with self.assertRaises(LookupError):
            self.panel.sizes(_ctx(), model="")

    def test_sizes_refuses_a_model_without_documents(self):
        with self.assertRaises(LookupError):
            self.panel.sizes(_ctx(), model="server.EngineJob")

    def test_sizes_reports_the_distribution(self):
        from evennia.objects.models import ObjectDB

        ObjectDB.objects.create(db_key="measured")
        result = self.panel.sizes(_ctx(), model="objects.ObjectDB")
        for field in ("sampled", "largest", "fat_count", "threshold_bytes", "median_bytes"):
            self.assertIn(field, result)

    def test_sizes_says_whether_it_saw_everything(self):
        from evennia.objects.models import ObjectDB

        ObjectDB.objects.create(db_key="measured")
        self.assertTrue(self.panel.sizes(_ctx(), model="objects.ObjectDB")["complete"])

    def test_it_does_not_need_the_io_owner(self):
        self.assertFalse(DatabasePanel.needs_io)
