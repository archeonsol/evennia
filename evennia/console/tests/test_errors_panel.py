"""Tests for the error inbox.

The parser carries the risk. It reads a text format nobody controls, and every
way it can be wrong is silent: a missed traceback is an error nobody sees, a
mis-grouped one is a count that lies, and an over-eager match turns ordinary log
prose into a fault that never happened.

Grouping is asserted on the property that makes it useful -- the same fault
from a shifted line number stays one row, a different call path does not.

"""

from django.test import TestCase

from evennia.console.models import ConsoleErrorState
from evennia.console.panels.errors import (
    ErrorsPanel,
    Occurrence,
    parse_tracebacks,
    strip_prefix,
)
from evennia.console.registry import CONSOLE_ACCESS, IOContext, WorkerContext

PREFIX = "2026-08-20 07:03:22 [!!] "


def _traceback(func="reclaim_jobs", line=572, exception="NotImplementedError", prefix=PREFIX):
    """Build one realistically formatted traceback block."""
    return [
        f"{prefix}Traceback (most recent call last):",
        f'{prefix}  File "C:\\moo\\evennia\\evennia\\jobs\\queue.py", line {line}, in {func}',
        f"{prefix}    r = get_redis_connection(alias)",
        f"{prefix}{exception}: This backend does not support this feature",
    ]


def _ctx(**params):
    """Build a worker context."""
    return WorkerContext(
        actor_id=1, actor_name="t", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


class TestPrefix(TestCase):
    """Log lines carry a timestamp and a level marker."""

    def test_splits_a_prefixed_line(self):
        when, body = strip_prefix(PREFIX + "hello")
        self.assertEqual(when, "2026-08-20 07:03:22")
        self.assertEqual(body, "hello")

    def test_an_unprefixed_line_is_left_alone(self):
        when, body = strip_prefix("no prefix here")
        self.assertEqual(when, "")
        self.assertEqual(body, "no prefix here")

    def test_other_level_markers(self):
        for marker in ("[..]", "[!!]", "[EE]"):
            _, body = strip_prefix(f"2026-08-20 07:03:22 {marker} text")
            self.assertEqual(body, "text")


class TestParser(TestCase):
    """Finding tracebacks in a stream of ordinary log lines."""

    def test_finds_one_traceback(self):
        found = parse_tracebacks(_traceback())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].exception, "NotImplementedError")
        self.assertIn("does not support", found[0].message)

    def test_captures_frames(self):
        found = parse_tracebacks(_traceback())
        self.assertEqual(found[0].frames[0][2], "reclaim_jobs")
        self.assertEqual(found[0].frames[0][1], 572)

    def test_ignores_surrounding_prose(self):
        lines = [PREFIX + "server started"] + _traceback() + [PREFIX + "carrying on"]
        self.assertEqual(len(parse_tracebacks(lines)), 1)

    def test_ordinary_lines_are_not_faults(self):
        # An over-eager matcher turns log prose into errors that never happened.
        lines = [
            PREFIX + "Connection established",
            PREFIX + "ValueError is a Python builtin",
            PREFIX + "server_maintenance redis liveness check",
        ]
        self.assertEqual(parse_tracebacks(lines), [])

    def test_finds_several_in_sequence(self):
        lines = _traceback() + [PREFIX + "between"] + _traceback(func="other", line=10)
        self.assertEqual(len(parse_tracebacks(lines)), 2)

    def test_an_unterminated_traceback_is_not_reported(self):
        # A block cut off by log rotation must not be reported half-parsed.
        lines = _traceback()[:-1]
        self.assertEqual(parse_tracebacks(lines), [])

    def test_source_is_recorded(self):
        found = parse_tracebacks(_traceback(), source="server")
        self.assertEqual(found[0].source, "server")

    def test_empty_input(self):
        self.assertEqual(parse_tracebacks([]), [])


class TestSignature(TestCase):
    """Grouping identity, and what it deliberately ignores."""

    def _occurrence(self, func="f", line=1, exception="ValueError", path="a.py"):
        return Occurrence(exception=exception, message="m", frames=((path, line, func),))

    def test_the_same_fault_groups(self):
        self.assertEqual(
            self._occurrence().signature(),
            self._occurrence().signature(),
        )

    def test_a_shifted_line_number_still_groups(self):
        # The point of excluding line numbers: an edit above a fault must not
        # report the same bug as brand new.
        self.assertEqual(
            self._occurrence(line=10).signature(),
            self._occurrence(line=400).signature(),
        )

    def test_a_different_function_does_not_group(self):
        self.assertNotEqual(
            self._occurrence(func="one").signature(),
            self._occurrence(func="two").signature(),
        )

    def test_a_different_exception_does_not_group(self):
        self.assertNotEqual(
            self._occurrence(exception="ValueError").signature(),
            self._occurrence(exception="TypeError").signature(),
        )

    def test_a_different_file_does_not_group(self):
        self.assertNotEqual(
            self._occurrence(path="a.py").signature(),
            self._occurrence(path="b.py").signature(),
        )


class TestPanel(TestCase):
    """Grouping, counting, and the review workflow."""

    def setUp(self):
        self.panel = ErrorsPanel()

    def _rows(self, lines, **params):
        """Run the panel against a fixed set of log lines."""
        from unittest.mock import patch

        with (
            patch("evennia.console.panels.errors._log_files", return_value={"server": "x"}),
            patch("evennia.console.panels.errors._tail_lines", return_value=lines),
        ):
            return self.panel.rows(_ctx(**params))

    def test_many_occurrences_become_one_row(self):
        # The reason the panel exists: a fault that fires constantly is one row
        # with a count, not a wall of identical lines.
        result = self._rows(_traceback() * 50)
        self.assertEqual(result["group_count"], 1)
        self.assertEqual(result["occurrence_count"], 50)
        self.assertEqual(result["rows"][0]["count"], 50)

    def test_distinct_faults_stay_distinct(self):
        result = self._rows(_traceback() + _traceback(func="elsewhere"))
        self.assertEqual(result["group_count"], 2)

    def test_search_filters_by_exception_and_message(self):
        lines = _traceback() + _traceback(exception="ValueError", func="other")
        self.assertEqual(self._rows(lines, search="valueerror")["group_count"], 1)

    def test_a_fault_starts_open(self):
        self.assertEqual(self._rows(_traceback())["rows"][0]["state"], "open")

    def test_review_records_a_judgement(self):
        signature = self._rows(_traceback())["rows"][0]["signature"]
        io = IOContext(actor_id=4, actor_name="operator", capabilities=frozenset())
        result = self.panel.review(
            io, signature=signature, state="muted", note="known, Redis is off"
        )
        self.assertEqual(result["state"], "muted")
        self.assertEqual(
            ConsoleErrorState.objects.get(signature=signature).note, "known, Redis is off"
        )

    def test_a_reviewed_state_comes_back_with_the_group(self):
        signature = self._rows(_traceback())["rows"][0]["signature"]
        io = IOContext(actor_id=4, actor_name="operator", capabilities=frozenset())
        self.panel.review(io, signature=signature, state="acknowledged")
        row = self._rows(_traceback())["rows"][0]
        self.assertEqual(row["state"], "acknowledged")
        self.assertEqual(row["reviewed_by"], "operator")

    def test_state_filter(self):
        signature = self._rows(_traceback())["rows"][0]["signature"]
        io = IOContext(actor_id=4, actor_name="operator", capabilities=frozenset())
        self.panel.review(io, signature=signature, state="muted")
        self.assertEqual(self._rows(_traceback(), state="muted")["group_count"], 1)
        self.assertEqual(self._rows(_traceback(), state="open")["group_count"], 0)

    def test_review_needs_a_signature(self):
        io = IOContext(actor_id=1, actor_name="t", capabilities=frozenset())
        with self.assertRaises(LookupError):
            self.panel.review(io, state="muted")

    def test_review_rejects_an_unknown_state(self):
        io = IOContext(actor_id=1, actor_name="t", capabilities=frozenset())
        with self.assertRaises(LookupError):
            self.panel.review(io, signature="abc", state="banana")

    def test_reviewing_twice_updates_rather_than_duplicates(self):
        io = IOContext(actor_id=1, actor_name="t", capabilities=frozenset())
        self.panel.review(io, signature="abc", state="muted")
        self.panel.review(io, signature="abc", state="acknowledged")
        self.assertEqual(ConsoleErrorState.objects.filter(signature="abc").count(), 1)
