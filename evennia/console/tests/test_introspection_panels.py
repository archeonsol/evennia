"""Tests for the introspection panels.

Every one of these reads a registry owned by another part of the engine, and
every registry has a shape. Guessing that shape fails *silently*: the panel
renders, the operator reads it, and it is wrong. This module was written three
times because of exactly that, so the tests here assert the shapes rather than
the plumbing.

Two of them lock in specific self-contradictions that shipped and were caught:
an orphan check that reported every game typeclass as missing, and a resolver
that said "no match" while suggesting the verb it had just matched.

"""

from django.test import TestCase

from evennia.console import runtime_spec
from evennia.console.panels.introspection import (
    ActionsPanel,
    HooksPanel,
    ObjectsPanel,
    PrototypesPanel,
)
from evennia.console.registry import CONSOLE_ACCESS, IOContext, WorkerContext


def _ctx(**params):
    """Build a worker context."""
    return WorkerContext(
        actor_id=1, actor_name="op", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


def _io():
    """Build an IO context."""
    return IOContext(actor_id=1, actor_name="op", capabilities=frozenset())


class TestTypeclasses(TestCase):
    """The typeclass tree, its counts, and its orphans."""

    def test_lists_importable_typeclasses(self):
        # Asserted against the base models, which are always loaded. The wider
        # list depends on what this process has imported, which is why the
        # panel says so rather than presenting it as the whole truth.
        paths = {item.path for item in runtime_spec.typeclass_specs()}
        self.assertIn("evennia.objects.models.ObjectDB", paths)

    def test_parents_are_recorded(self):
        by_path = {item.path: item for item in runtime_spec.typeclass_specs()}
        self.assertTrue(by_path["evennia.objects.models.ObjectDB"].parents)

    def test_the_importable_list_declares_its_own_limits(self):
        self.assertIn("import", ObjectsPanel().rows(_ctx())["importable_caveat"])

    def test_counts_group_on_the_indexed_column(self):
        from evennia.objects.models import ObjectDB

        ObjectDB.objects.create(
            db_key="counted", db_typeclass_path="evennia.objects.objects.DefaultObject"
        )
        counts = runtime_spec.stored_typeclass_counts()
        self.assertEqual(counts.get("evennia.objects.objects.DefaultObject"), 1)

    def test_a_path_that_imports_is_not_an_orphan(self):
        # The bug this locks in: get_all_typeclasses scans engine modules only,
        # so a game's own typeclasses are absent from it while importing fine.
        # An orphan check built on membership called every one of them missing.
        from evennia.objects.models import ObjectDB

        ObjectDB.objects.create(
            db_key="fine", db_typeclass_path="evennia.objects.objects.DefaultObject"
        )
        self.assertEqual(runtime_spec.orphan_typeclass_paths(), ())

    def test_a_path_that_does_not_import_is_an_orphan(self):
        from evennia.objects.models import ObjectDB

        ObjectDB.objects.create(db_key="gone", db_typeclass_path="nowhere.at.All")
        orphans = {row["path"] for row in runtime_spec.orphan_typeclass_paths()}
        self.assertIn("nowhere.at.All", orphans)

    def test_the_panel_reports_both(self):
        result = ObjectsPanel().rows(_ctx())
        self.assertTrue(result["typeclass_count"])
        self.assertIn("orphans", result)

    def test_search_filters(self):
        result = ObjectsPanel().rows(_ctx(search="objectdb"))
        self.assertTrue(result["rows"])
        for row in result["rows"]:
            self.assertIn("objectdb", (row["path"] + row["name"]).lower())


class TestActions(TestCase):
    """The action registry, and what one typed line reaches."""

    def setUp(self):
        self.panel = ActionsPanel()

    def test_lists_registered_actions(self):
        result = self.panel.rows(_ctx())
        self.assertTrue(result["available"])
        self.assertTrue(result["action_count"])

    def test_verbs_come_from_the_registry_not_the_class(self):
        # An action class carries no verbs of its own, so reading them off the
        # class yields an empty list for every action -- a table of nothing
        # that looks like a table.
        result = self.panel.rows(_ctx())
        with_verbs = [row for row in result["rows"] if row["verbs"]]
        self.assertTrue(with_verbs, "no action reported any verb")

    def test_search_matches_a_verb(self):
        result = self.panel.rows(_ctx(search="drop"))
        self.assertTrue(result["rows"])

    def test_resolving_a_known_verb(self):
        result = self.panel.resolve(_io(), text="drop sword")
        self.assertIsNotNone(result["matched"])
        self.assertEqual(result["matched"]["action"], "Drop")
        self.assertIn("Drop", result["explanation"])

    def test_resolving_an_abbreviation(self):
        result = self.panel.resolve(_io(), text="co somebody")
        self.assertIsNotNone(result["matched"])

    def test_an_unknown_verb_matches_nothing(self):
        result = self.panel.resolve(_io(), text="zzzz thing")
        self.assertIsNone(result["matched"])
        self.assertIn("no registered verb", result["explanation"])

    def test_the_explanation_never_contradicts_the_match(self):
        # The bug this locks in: match_verb returns a (verb, class, score)
        # tuple, and reading it as an object produced "no match" alongside a
        # suggestion naming the very verb that had matched.
        for text in ("drop sword", "look", "co name", "zzzz thing"):
            result = self.panel.resolve(_io(), text=text)
            if result["matched"]:
                self.assertIn("reaches", result["explanation"])
                self.assertEqual(result["suggestions"], [])
            else:
                self.assertIn("no registered verb", result["explanation"])
                self.assertNotIn(result["verb"], result["suggestions"])

    def test_resolving_runs_nothing(self):
        # It reports a match. If it dispatched, an operator debugging "why did
        # that not work" would perform the thing they were investigating.
        before = ActionsPanel().rows(_ctx())["action_count"]
        self.panel.resolve(_io(), text="drop sword")
        self.assertEqual(ActionsPanel().rows(_ctx())["action_count"], before)

    def test_empty_input_is_refused(self):
        with self.assertRaises(ValueError):
            self.panel.resolve(_io(), text="   ")


class TestHooks(TestCase):
    """The H1 registry, read through its public API."""

    def setUp(self):
        self.panel = HooksPanel()

    def test_lists_hooks(self):
        result = self.panel.rows(_ctx())
        self.assertTrue(result["available"])
        self.assertTrue(result["hook_count"])

    def test_a_hook_carries_its_contract(self):
        row = self.panel.rows(_ctx())["rows"][0]
        for field in ("event", "phase", "returns", "discipline"):
            self.assertIn(field, row)

    def test_hooks_are_named_by_their_call_site(self):
        # list_all returns HookSpec records with no name of their own, so
        # enumerating them gave every hook an integer for a name.
        rows = self.panel.rows(_ctx())["rows"]
        self.assertFalse(any(row["name"].isdigit() for row in rows))
        self.assertTrue(any("." in row["name"] for row in rows))

    def test_events_are_offered_for_filtering(self):
        result = self.panel.rows(_ctx())
        self.assertTrue(result["events"])

    def test_filtering_by_event(self):
        events = self.panel.rows(_ctx())["events"]
        result = self.panel.rows(_ctx(event=events[0]))
        self.assertTrue(result["rows"])
        for row in result["rows"]:
            self.assertEqual(row["event"], events[0])

    def test_lint_findings_are_surfaced(self):
        self.assertIn("findings", self.panel.rows(_ctx()))


class TestPrototypes(TestCase):
    """Read-only, and honest when there are none."""

    def test_reports_availability(self):
        result = PrototypesPanel().rows(_ctx())
        self.assertIn("available", result)
        self.assertIn("count", result)

    def test_it_says_why_it_is_read_only(self):
        self.assertIn("version-controlled", PrototypesPanel().rows(_ctx())["note"])

    def test_it_offers_no_write_action(self):
        # The rule exists to keep prototypes in version control; a write action
        # here would quietly undo it.
        for name in dir(PrototypesPanel):
            self.assertNotIn(name, {"save", "create", "delete", "spawn"})


class TestDegradedMode(TestCase):
    """All four read registries and classes, so an outage does not hide them."""

    def test_none_need_the_io_owner(self):
        for panel in (ObjectsPanel, ActionsPanel, HooksPanel, PrototypesPanel):
            self.assertFalse(panel.needs_io, f"{panel.key} would break degraded mode")
