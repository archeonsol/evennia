"""Tests for the Records behaviours that only matter once a table is large.

Each of these covers a real defect the first version shipped with: counting on
every page view, paging by offset, OR-ing an unindexable LIKE across every text
column, and rendering thirty-seven columns. All four are invisible on a
development database with twelve rows, which is exactly why they are asserted
rather than eyeballed.

"""

from unittest.mock import patch

from django.core.exceptions import FieldError
from django.test import TestCase

from evennia.console import spec
from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.records import RecordsPanel
from evennia.console.registry import CONSOLE_ACCESS, WorkerContext


def _ctx(**params):
    """Build a worker context carrying panel parameters."""
    return WorkerContext(
        actor_id=1,
        actor_name="tester",
        capabilities=frozenset({CONSOLE_ACCESS}),
        params=params,
    )


class RecordsScaleTestCase(TestCase):
    """Twelve rows, enough to page several times."""

    def setUp(self):
        self.panel = RecordsPanel()
        for index in range(12):
            ConsoleAuditEvent.objects.create(
                event_id=f"{index:032d}", panel=f"p{index:02d}", operation="op"
            )


class TestPaging(RecordsScaleTestCase):
    """Keyset paging, and no counting to do it."""

    def test_a_page_does_not_count(self):
        # COUNT(*) has no shortcut on PostgreSQL, so a count per page view is a
        # table scan per page view.
        from django.db.models.query import QuerySet

        with patch.object(QuerySet, "count", side_effect=AssertionError("count() is forbidden")):
            self.panel.rows(_ctx(model="console.consoleauditevent"))

    def test_has_more_comes_from_one_extra_row(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", page_size=5))
        self.assertEqual(len(result["rows"]), 5)
        self.assertTrue(result["has_more"])
        self.assertTrue(result["next_cursor"])
        self.assertNotIn("total", result)

    def test_cursor_walks_every_row_exactly_once(self):
        seen, cursor = [], ""
        for _ in range(10):
            result = self.panel.rows(
                _ctx(model="console.consoleauditevent", page_size=5, cursor=cursor)
            )
            seen.extend(row["id"] for row in result["rows"])
            cursor = result["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12, "a row was repeated across pages")

    def test_cursor_is_correct_under_a_secondary_sort(self):
        # The dangerous case: equal sort values need the primary key as a
        # tiebreaker or the cursor skips and repeats rows.
        seen, cursor = [], ""
        for _ in range(10):
            result = self.panel.rows(
                _ctx(
                    model="console.consoleauditevent", page_size=5, order="operation", cursor=cursor
                )
            )
            seen.extend(row["id"] for row in result["rows"])
            cursor = result["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12, "equal sort values broke the cursor")

    def test_descending_sort_pages_correctly(self):
        seen, cursor = [], ""
        for _ in range(10):
            result = self.panel.rows(
                _ctx(model="console.consoleauditevent", page_size=5, order="-panel", cursor=cursor)
            )
            seen.extend(row["id"] for row in result["rows"])
            cursor = result["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(set(seen)), 12)

    def test_a_malformed_cursor_starts_over(self):
        # A cursor is a position, not a credential. A stale one means "from the
        # beginning", never an error page.
        result = self.panel.rows(_ctx(model="console.consoleauditevent", cursor="not-a-cursor"))
        self.assertEqual(len(result["rows"]), 12)

    def test_last_page_reports_no_more(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", page_size=100))
        self.assertFalse(result["has_more"])
        self.assertEqual(result["next_cursor"], "")


class TestOrdering(RecordsScaleTestCase):
    """Deterministic ordering, which keyset paging depends on."""

    def setUp(self):
        super().setUp()
        self.spec = spec.get_model_spec("console.consoleauditevent")

    def test_ordering_always_ends_in_the_primary_key(self):
        self.assertEqual(self.panel._ordering(self.spec, "panel"), ["panel", "id"])
        self.assertEqual(self.panel._ordering(self.spec, "-panel"), ["-panel", "-id"])

    def test_ordering_by_the_key_needs_no_tiebreaker(self):
        self.assertEqual(self.panel._ordering(self.spec, "id"), ["id"])

    def test_nullable_sort_column_falls_back_to_the_key(self):
        # NULL has no position in a range comparison, so a cursor over a
        # nullable column would page wrongly rather than slowly.
        self.assertEqual(self.panel._ordering(self.spec, "inverse"), ["-id"])

    def test_unknown_ordering_field_is_rejected(self):
        with self.assertRaises(FieldError):
            self.panel.rows(_ctx(model="console.consoleauditevent", order="nope"))


class TestCounting(RecordsScaleTestCase):
    """Counting is a separate, deliberate request."""

    def test_exact_on_a_small_table(self):
        result = self.panel.count(_ctx(), model="console.consoleauditevent")
        self.assertTrue(result["exact"])
        self.assertEqual(result["rows"], 12)

    def test_estimates_past_the_ceiling_and_says_so(self):
        with patch.object(RecordsPanel, "_estimate", return_value=5_000_000):
            result = self.panel.count(_ctx(), model="console.consoleauditevent")
        self.assertFalse(result["exact"])
        self.assertEqual(result["rows"], 5_000_000)
        self.assertIn("only when asked", result["reason"])

    def test_an_exact_count_can_be_demanded(self):
        with patch.object(RecordsPanel, "_estimate", return_value=5_000_000):
            result = self.panel.count(_ctx(), model="console.consoleauditevent", exact=True)
        self.assertTrue(result["exact"])
        self.assertEqual(result["rows"], 12)

    def test_a_narrowed_count_is_always_exact(self):
        # A table estimate describes the whole table, so it means nothing once
        # a filter is applied.
        with patch.object(RecordsPanel, "_estimate", return_value=5_000_000):
            result = self.panel.count(_ctx(), model="console.consoleauditevent", search="p01")
        self.assertTrue(result["exact"])
        self.assertEqual(result["rows"], 1)


class TestSearch(RecordsScaleTestCase):
    """Search is scoped to what an index can serve."""

    def test_scans_identity_columns_only(self):
        model_spec = spec.get_model_spec("server.sessionrecord")
        scanned = self.panel._search_fields(model_spec)
        char_columns = [f.name for f in model_spec.fields if f.kind == "CharField"]
        self.assertLess(len(scanned), len(char_columns))
        self.assertIn("account_name", scanned)

    def test_never_scans_an_unbounded_text_field(self):
        for label in ("console.consoleauditevent", "server.sanction", "server.sessionrecord"):
            model_spec = spec.get_model_spec(label)
            kinds = {f.name: f.kind for f in model_spec.fields}
            for name in self.panel._search_fields(model_spec):
                self.assertNotEqual(kinds[name], "TextField", f"{label}.{name} is unbounded")

    def test_matches_by_prefix(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", search="p0"))
        self.assertEqual(len(result["rows"]), 10)

    def test_does_not_match_mid_string(self):
        # A prefix match is indexable; a contains match is not. The panel
        # trades that reach for a query that stays usable at scale.
        result = self.panel.rows(_ctx(model="console.consoleauditevent", search="01"))
        self.assertEqual(len(result["rows"]), 0)


class TestFilters(RecordsScaleTestCase):
    """Field-scoped filters, validated against the model."""

    def test_exact_filter(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", **{"f.panel": "p03"}))
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["filters"], {"panel": "p03"})

    def test_lookup_suffix(self):
        result = self.panel.rows(
            _ctx(model="console.consoleauditevent", **{"f.panel__startswith": "p0"})
        )
        self.assertEqual(len(result["rows"]), 10)

    def test_isnull_coerces_its_value(self):
        result = self.panel.rows(
            _ctx(model="console.consoleauditevent", **{"f.inverse__isnull": "true"})
        )
        self.assertEqual(len(result["rows"]), 12)

    def test_in_splits_on_commas(self):
        result = self.panel.rows(
            _ctx(model="console.consoleauditevent", **{"f.panel__in": "p00,p01"})
        )
        self.assertEqual(len(result["rows"]), 2)

    def test_unknown_field_is_a_stated_error(self):
        with self.assertRaises(FieldError):
            self.panel.rows(_ctx(model="console.consoleauditevent", **{"f.nope": "x"}))

    def test_unsupported_comparison_is_a_stated_error(self):
        with self.assertRaises(FieldError):
            self.panel.rows(_ctx(model="console.consoleauditevent", **{"f.panel__regex": "x"}))


class TestColumns(RecordsScaleTestCase):
    """A wide model is readable by default."""

    def test_a_wide_model_does_not_render_every_column(self):
        model_spec = spec.get_model_spec("server.sessionrecord")
        columns = self.panel._columns(model_spec, None)
        self.assertLessEqual(len(columns), 8)
        self.assertLess(len(columns), len(model_spec.fields))

    def test_defaults_lead_with_the_key_and_an_identity(self):
        columns = self.panel._columns(spec.get_model_spec("server.sessionrecord"), None)
        self.assertEqual(columns[0], "id")
        self.assertIn("account_name", columns)

    def test_columns_can_be_chosen(self):
        result = self.panel.rows(
            _ctx(model="console.consoleauditevent", columns="event_id,operation")
        )
        self.assertEqual(result["columns"], ["event_id", "operation"])
        self.assertEqual(set(result["rows"][0]), {"event_id", "operation"})

    def test_unknown_requested_columns_fall_back(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", columns="nope,also_nope"))
        self.assertIn("id", result["columns"])

    def test_the_full_column_list_is_offered(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent"))
        self.assertIn("before", result["available_columns"])

    def test_the_key_is_always_fetched_even_when_not_shown(self):
        # The cursor needs it, so paging breaks silently without it.
        result = self.panel.rows(
            _ctx(model="console.consoleauditevent", columns="panel", page_size=5)
        )
        self.assertTrue(result["next_cursor"])
