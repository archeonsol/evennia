"""Tests for saved views and operator presence.

Presence has one property that matters more than the rest: it must cost no
database writes. A heartbeat per open console per interval, written to a table,
is a steady load whose only purpose is to say "still here". The test below
asserts the write count is zero rather than trusting the implementation to stay
that way.

Saved views store the address bar rather than a query, so the test that matters
is that the stored string is opaque to the model: anything that parsed it would
have to be kept in step with every panel that reads those keys.

"""

from django.core.cache import cache
from django.test import TestCase

from evennia.console import presence
from evennia.console.models import ConsoleAuditEvent, ConsoleSavedView
from evennia.console.panels.views import ViewsPanel
from evennia.console.registry import CONSOLE_ACCESS, WorkerContext, is_io_action


def _ctx(actor_id=1, actor_name="op", **params):
    """Build a worker context."""

    return WorkerContext(
        actor_id=actor_id,
        actor_name=actor_name,
        capabilities=frozenset({CONSOLE_ACCESS}),
        params=params,
    )


class TestSavedViews(TestCase):
    """Naming a filter combination and getting it back."""

    def setUp(self):
        self.panel = ViewsPanel()

    def test_saving_stores_the_address(self):
        view = self.panel.save(
            _ctx(), name="Open flags", panel="moderation", query="modState=open"
        )
        self.assertEqual(view["query"], "modState=open")
        self.assertEqual(view["url"], "#moderation?modState=open")

    def test_a_view_with_no_query_still_has_a_url(self):
        view = self.panel.save(_ctx(), name="All", panel="records")
        self.assertEqual(view["url"], "#records")

    def test_saving_the_same_name_replaces_it(self):
        self.panel.save(_ctx(), name="Open flags", panel="moderation", query="a=1")
        self.panel.save(_ctx(), name="Open flags", panel="moderation", query="a=2")
        self.assertEqual(ConsoleSavedView.objects.count(), 1)
        self.assertEqual(ConsoleSavedView.objects.get().query, "a=2")

    def test_the_same_name_on_another_panel_is_a_different_view(self):
        self.panel.save(_ctx(), name="Recent", panel="moderation")
        self.panel.save(_ctx(), name="Recent", panel="audit")
        self.assertEqual(ConsoleSavedView.objects.count(), 2)

    def test_a_name_is_required(self):
        with self.assertRaises(ValueError):
            self.panel.save(_ctx(), name="  ", panel="records")

    def test_a_panel_is_required(self):
        with self.assertRaises(ValueError):
            self.panel.save(_ctx(), name="Named", panel="")

    def test_filtering_by_panel(self):
        self.panel.save(_ctx(), name="A", panel="records")
        self.panel.save(_ctx(), name="B", panel="audit")
        rows = self.panel.rows(_ctx(panel="audit"))["rows"]
        self.assertEqual([row["name"] for row in rows], ["B"])

    def test_pinned_views_are_separated(self):
        self.panel.save(_ctx(), name="Pinned", panel="records", pinned=True)
        self.panel.save(_ctx(), name="Not pinned", panel="records")
        result = self.panel.rows(_ctx())
        self.assertEqual([row["name"] for row in result["pinned"]], ["Pinned"])
        self.assertEqual(len(result["rows"]), 2)

    def test_the_creator_is_recorded(self):
        view = self.panel.save(_ctx(actor_id=7, actor_name="seven"), name="A", panel="records")
        self.assertEqual(view["created_by_name"], "seven")

    def test_forgetting_removes_it(self):
        view = self.panel.save(_ctx(), name="A", panel="records")
        self.panel.forget(_ctx(), view_id=view["id"])
        self.assertFalse(ConsoleSavedView.objects.exists())

    def test_forgetting_something_absent_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.forget(_ctx(), view_id=999999)

    def test_saving_and_forgetting_are_recorded(self):
        view = self.panel.save(_ctx(), name="A", panel="records")
        self.panel.forget(_ctx(), view_id=view["id"])
        operations = set(
            ConsoleAuditEvent.objects.filter(panel="views").values_list("operation", flat=True)
        )
        self.assertEqual(operations, {"add", "delete"})

    def test_the_query_is_stored_without_being_parsed(self):
        # Anything that parsed the stored string would have to stay in step
        # with every panel that reads those keys.
        odd = "a=1&b=%20&c=;;"
        view = self.panel.save(_ctx(), name="Odd", panel="records", query=odd)
        self.assertEqual(view["query"], odd)

    def test_it_does_not_need_the_io_owner(self):
        self.assertFalse(ViewsPanel.needs_io)
        self.assertFalse(is_io_action(ViewsPanel.save))


class TestPresence(TestCase):
    """Who else is here, at no cost to the database."""

    def setUp(self):
        cache.clear()
        self.panel = ViewsPanel()

    def tearDown(self):
        cache.clear()

    def test_a_heartbeat_makes_an_operator_visible_to_others(self):
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"), panel="records")
        others = self.panel.heartbeat(_ctx(actor_id=2, actor_name="two"))["present"]
        self.assertEqual([entry["actor_name"] for entry in others], ["one"])

    def test_an_operator_does_not_see_themselves(self):
        result = self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"))
        self.assertEqual(result["present"], [])

    def test_the_panel_they_have_open_is_reported(self):
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"), panel="moderation")
        others = self.panel.heartbeat(_ctx(actor_id=2))["present"]
        self.assertEqual(others[0]["panel"], "moderation")

    def test_two_operators_on_one_record_see_each_other(self):
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"), record="objects.objectdb#5")
        result = self.panel.heartbeat(_ctx(actor_id=2), record="objects.objectdb#5")
        self.assertEqual([entry["actor_name"] for entry in result["on_this_record"]], ["one"])

    def test_operators_on_different_records_do_not(self):
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"), record="objects.objectdb#5")
        result = self.panel.heartbeat(_ctx(actor_id=2), record="objects.objectdb#6")
        self.assertEqual(result["on_this_record"], [])

    def test_claiming_reports_the_other_operator(self):
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"), record="objects.objectdb#5")
        result = self.panel.claim(_ctx(actor_id=2), record="objects.objectdb#5")
        self.assertIn("one", result["advice"])

    def test_a_claim_is_advice_and_never_a_lock(self):
        # A lock nobody can release outlives the person who took it. The write
        # path still rejects a stale write; this only means the operator finds
        # out first.
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"), record="objects.objectdb#5")
        self.assertFalse(self.panel.claim(_ctx(actor_id=2), record="objects.objectdb#5")["is_lock"])

    def test_an_empty_record_claims_nothing(self):
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"), record="")
        self.assertEqual(self.panel.claim(_ctx(actor_id=2), record="")["others"], [])

    def test_departing_removes_the_operator_at_once(self):
        # Without this the entry lingers for its time to live, and somebody who
        # left five seconds ago still reads as editing the row.
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"))
        self.panel.depart(_ctx(actor_id=1))
        self.assertEqual(self.panel.heartbeat(_ctx(actor_id=2))["present"], [])

    def test_presence_writes_nothing_to_the_database(self):
        # The property that makes a thirty-second heartbeat acceptable.
        with self.assertNumQueries(0):
            presence.touch(1, "one", panel="records", record="objects.objectdb#5")
            presence.present(exclude=2)
            presence.on_record("objects.objectdb#5", exclude=2)
            presence.leave(1)

    def test_a_heartbeat_is_not_audited(self):
        # A heartbeat every thirty seconds per console would bury every real
        # operation in the trail.
        self.panel.heartbeat(_ctx(actor_id=1, actor_name="one"))
        self.assertFalse(ConsoleAuditEvent.objects.filter(operation="heartbeat").exists())

    def test_the_client_is_told_the_interval(self):
        # One number in one place. Two copies would drift.
        self.assertEqual(
            self.panel.heartbeat(_ctx())["heartbeat_seconds"], presence.HEARTBEAT
        )

    def test_the_interval_is_shorter_than_the_expiry(self):
        # Otherwise one missed beat makes somebody vanish mid-edit.
        self.assertLess(presence.HEARTBEAT, presence.TTL)
