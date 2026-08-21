"""Tests for the Runtime panel.

This panel had no tests, which is how it shipped reporting "the scheduler does
not expose a readable registry" about a scheduler that exposes one. The names
it guessed at -- ``_SYSTEMS``, ``REGISTRY``, ``_REGISTRY`` -- are all wrong;
the module's public reader is ``all_systems()``.

That is the fifth time in this package that guessing a registry's shape
produced a panel which rendered happily while telling the operator something
untrue. The tests below assert the shape.

"""

from django.test import TestCase

from evennia.console.panels.runtime import RuntimePanel, _plain
from evennia.console.registry import CONSOLE_ACCESS, WorkerContext
from evennia.utils import systems


def _ctx(**params):
    """Build a worker context."""

    return WorkerContext(
        actor_id=1, actor_name="op", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


class TestScheduler(TestCase):
    """The scheduler registry, read through its public API."""

    def setUp(self):
        systems.load_system_modules()
        self.panel = RuntimePanel()

    def test_the_registry_is_readable(self):
        # The exact regression: a wrong private name made every read report
        # the scheduler as unreadable.
        result = self.panel.rows(_ctx())["systems"]
        self.assertTrue(result["available"], result.get("reason", ""))

    def test_systems_are_listed(self):
        rows = self.panel.rows(_ctx())["systems"]["rows"]
        self.assertTrue(rows, "the scheduler reported no system at all")
        self.assertIn("flush-attributes", {row["name"] for row in rows})

    def test_a_system_carries_its_cadence_and_overlap_state(self):
        row = self.panel.rows(_ctx())["systems"]["rows"][0]
        for field in ("cadence", "scope", "workload", "fires", "skips", "in_flight"):
            self.assertIn(field, row)

    def test_cadence_is_a_label_not_a_repr(self):
        # Cadence stringifies as "<Cadence every 60s>". A table column headed
        # CADENCE does not need the class name repeated inside every cell.
        row = self.panel.rows(_ctx())["systems"]["rows"][0]
        self.assertNotIn("<", row["cadence"])
        self.assertNotIn("<", row["scope"])
        self.assertTrue(row["cadence"])

    def test_an_empty_registry_is_not_a_failure(self):
        # Only the Server process loads the scheduler modules, so nothing
        # registered is a real answer in a process that never loads them. It
        # must not look like a broken read.
        systems._clear_registry()
        result = self.panel.rows(_ctx())["systems"]
        self.assertTrue(result["available"])
        self.assertEqual(result["rows"], [])
        self.assertIn("Server process", result["reason"])


class TestPlainLabel(TestCase):
    """The repr stripper."""

    def test_it_strips_the_class_name(self):
        self.assertEqual(_plain("<Cadence every 60s>"), "every 60s")

    def test_it_leaves_an_ordinary_string_alone(self):
        self.assertEqual(_plain("global"), "global")

    def test_it_keeps_a_wrapper_that_carries_nothing_else(self):
        self.assertEqual(_plain("<Cadence>"), "Cadence")

    def test_it_survives_none(self):
        self.assertEqual(_plain(None), "")


class TestPayload(TestCase):
    """What the station renders comes from here."""

    def setUp(self):
        self.panel = RuntimePanel()

    def test_every_rendered_key_is_present(self):
        # The frontend read `systems` and `tasks` for the first time when this
        # was written; before that the panel returned both and the renderer
        # dropped them on the floor.
        result = self.panel.rows(_ctx())
        for key in ("alarms", "caches", "metrics", "systems", "tasks", "metrics_available"):
            self.assertIn(key, result)

    def test_task_roots_are_reported(self):
        tasks = self.panel.rows(_ctx())["tasks"]
        for key in ("active", "started", "db_scope_closes"):
            self.assertIn(key, tasks)

    def test_it_works_without_the_io_owner(self):
        self.assertFalse(RuntimePanel.needs_io)
