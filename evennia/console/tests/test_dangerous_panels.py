"""Tests for the four panels the security review gates.

These are the most dangerous things the engine serves over HTTP, so the tests
are about the controls rather than the features. Three properties, asserted for
every panel that has them:

* off unless a deployment explicitly permits it, because "may anyone here do
  this at all" is a different question from "who is this", and a capability
  cannot answer the first;
* recorded, with the submitted source text where there is any, because
  prevention is unavailable against somebody holding a REPL and legibility
  afterwards is;
* refusing to act without a stated reason, wherever the action is one somebody
  will later have to justify.

"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.dangerous import (
    PanelDisabled,
    ReplPanel,
    ServerPanel,
    SessionsPanel,
    SqlPanel,
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


class TestDefaultsOff(TestCase):
    """Nothing here runs on a deployment that did not ask for it."""

    def test_repl_is_off(self):
        self.assertFalse(ReplPanel().rows(_ctx())["enabled"])
        with self.assertRaises(PanelDisabled):
            ReplPanel().execute(_io(), source="1")

    def test_sql_is_off(self):
        self.assertFalse(SqlPanel().rows(_ctx())["enabled"])
        with self.assertRaises(PanelDisabled):
            SqlPanel().query(_io(), sql="select 1")

    def test_server_control_is_off(self):
        self.assertFalse(ServerPanel().rows(_ctx())["enabled"])
        with self.assertRaises(PanelDisabled):
            ServerPanel().control(_io(), action="reload", reason="x")

    def test_the_refusal_names_the_setting(self):
        # An operator who needs this on should not have to grep for the flag.
        with self.assertRaises(PanelDisabled) as caught:
            ReplPanel().execute(_io(), source="1")
        self.assertIn("CONSOLE_REPL_ENABLED", str(caught.exception))

    def test_server_status_is_readable_while_control_is_not(self):
        # Knowing what is running is not dangerous, and is most wanted exactly
        # when changing it would be.
        result = ServerPanel().rows(_ctx())
        self.assertIn("checks", result)
        self.assertFalse(result["enabled"])


@override_settings(CONSOLE_REPL_ENABLED=True)
class TestRepl(TestCase):
    """Running Python, and recording that it was run."""

    def test_evaluates_an_expression(self):
        result = ReplPanel().execute(_io(), source="2 + 2")
        self.assertEqual(result["output"], ["4"])
        self.assertEqual(result["outcome"], "success")

    def test_executes_a_statement(self):
        result = ReplPanel().execute(_io(), source="print('hello')")
        self.assertEqual(result["output"], ["hello"])

    def test_an_error_is_reported_not_raised(self):
        # The operator's code failing is a result, not a server fault.
        result = ReplPanel().execute(_io(), source="1/0")
        self.assertEqual(result["outcome"], "partial")
        self.assertIn("ZeroDivisionError", result["message"])

    def test_empty_source_is_refused(self):
        with self.assertRaises(ValueError):
            ReplPanel().execute(_io(), source="   ")

    def test_the_source_text_is_recorded(self):
        # The whole control: prevention is unavailable, legibility is.
        ReplPanel().execute(_io(), source="2 + 2")
        row = ConsoleAuditEvent.objects.get(panel="repl", operation="execute")
        self.assertEqual(row.before["source"], "2 + 2")
        self.assertEqual(row.actor_name, "op")

    def test_a_failed_submission_is_still_recorded(self):
        ReplPanel().execute(_io(), source="1/0")
        row = ConsoleAuditEvent.objects.get(panel="repl", operation="execute")
        self.assertEqual(row.outcome, ConsoleAuditEvent.OUTCOME_PARTIAL)
        self.assertIn("ZeroDivisionError", row.message)

    def test_repl_rows_prune_on_the_repl_window(self):
        ReplPanel().execute(_io(), source="1")
        row = ConsoleAuditEvent.objects.get(panel="repl", operation="execute")
        self.assertEqual(row.retention, ConsoleAuditEvent.RETENTION_REPL)

    def test_history_is_per_operator(self):
        ReplPanel().execute(_io(), source="1")
        mine = ReplPanel().rows(_ctx())["history"]
        self.assertEqual(len(mine), 1)
        other = ReplPanel().rows(
            WorkerContext(actor_id=999, capabilities=frozenset({CONSOLE_ACCESS}))
        )["history"]
        self.assertEqual(other, [])


@override_settings(CONSOLE_SQL_ENABLED=True)
class TestSql(TestCase):
    """Reading, and refusing to do anything else."""

    def test_runs_a_select(self):
        result = SqlPanel().query(_io(), sql="select 1 as one")
        self.assertEqual(result["columns"], ["one"])
        self.assertEqual(result["rows"], [[1]])

    def test_a_write_is_refused_before_it_is_sent(self):
        # Refused by prefix, so a typo cannot become a migration -- and on
        # PostgreSQL the read-only transaction refuses it a second time.
        for statement in (
            "delete from console_consoleauditevent",
            "drop table x",
            "update y set z=1",
        ):
            with self.assertRaises(ValueError) as caught:
                SqlPanel().query(_io(), sql=statement)
            self.assertIn("read-only", str(caught.exception))

    def test_allowed_statements_are_listed(self):
        self.assertIn("select", SqlPanel().rows(_ctx())["allowed"])

    def test_an_empty_query_is_refused(self):
        with self.assertRaises(ValueError):
            SqlPanel().query(_io(), sql="  ")

    def test_a_broken_query_is_reported_not_raised(self):
        result = SqlPanel().query(_io(), sql="select * from no_such_table")
        self.assertEqual(result["outcome"], "conflict")
        self.assertTrue(result["message"])

    def test_parameters_are_bound(self):
        result = SqlPanel().query(_io(), sql="select ? as v", parameters=["x"])
        self.assertEqual(result["rows"], [["x"]])

    def test_rows_are_capped(self):
        with override_settings(CONSOLE_SQL_MAX_ROWS=1):
            result = SqlPanel().query(_io(), sql="select 1 union select 2 union select 3")
        self.assertLessEqual(result["row_count"], 1)
        self.assertTrue(result["capped"])

    def test_the_query_text_is_recorded(self):
        SqlPanel().query(_io(), sql="select 1")
        row = ConsoleAuditEvent.objects.get(panel="sql", operation="query")
        self.assertEqual(row.before["query"], "select 1")

    def test_the_bounds_are_published(self):
        result = SqlPanel().rows(_ctx())
        self.assertTrue(result["timeout_ms"] > 0)
        self.assertTrue(result["max_rows"] > 0)


@override_settings(CONSOLE_SERVER_CONTROL_ENABLED=True)
class TestServerControl(TestCase):
    """Changing the server's state, recorded before it happens."""

    def _panel(self):
        return ServerPanel()

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            self._panel().control(_io(), action="explode", reason="x")

    def test_a_reason_is_required(self):
        with self.assertRaises(ValueError):
            self._panel().control(_io(), action="reload", reason="  ")

    def test_the_action_is_recorded_permanently(self):
        with patch.object(ServerPanel, "_request"):
            self._panel().control(_io(), action="reload", reason="deploying")
        row = ConsoleAuditEvent.objects.get(panel="server", operation="server_reload")
        self.assertEqual(row.retention, ConsoleAuditEvent.RETENTION_PERMANENT)
        self.assertEqual(row.message, "deploying")

    def test_it_is_recorded_before_it_is_requested(self):
        # A shutdown that succeeds takes the process with it, so a row written
        # afterwards would never be written at all.
        order = []
        with patch.object(ServerPanel, "_request", side_effect=lambda a: order.append("request")):
            with patch(
                "evennia.console.panels.dangerous.audit.record",
                side_effect=lambda **kw: order.append("audit"),
            ):
                self._panel().control(_io(), action="shutdown", reason="maintenance")
        self.assertEqual(order, ["audit", "request"])


class TestSessions(TestCase):
    """Watching and disconnecting, both recorded."""

    def test_reads_the_live_handler(self):
        result = SessionsPanel().rows(_ctx())
        self.assertIn("rows", result)

    def test_disconnecting_requires_a_reason(self):
        with self.assertRaises(ValueError):
            SessionsPanel().disconnect(_io(), sessid=1, reason="")

    def test_watching_requires_a_reason(self):
        with self.assertRaises(ValueError):
            SessionsPanel().watch(_io(), sessid=1, reason="")

    def test_watching_an_absent_session_is_a_lookup_error(self):
        with patch("evennia.server.sessionhandler.SESSIONS") as sessions:
            sessions.session_from_sessid.return_value = None
            with self.assertRaises(LookupError):
                SessionsPanel().watch(_io(), sessid=4242, reason="investigating")

    def test_watching_refuses_because_nothing_mirrors_session_output(self):
        # The control, the reason prompt and the permanent audit row were all
        # built before the thing they describe. Nothing in the engine copies a
        # session's output anywhere, so the action cannot do what it says.
        from types import SimpleNamespace

        account = SimpleNamespace(pk=7, username="player")
        with patch("evennia.server.sessionhandler.SESSIONS") as sessions:
            sessions.session_from_sessid.return_value = SimpleNamespace(account=account)
            with self.assertRaises(NotImplementedError):
                SessionsPanel().watch(_io(), sessid=3, reason="report of harassment")

    def test_a_refused_watch_records_nothing(self):
        # A permanent row saying an account was watched, when nobody saw
        # anything, is a false statement about a real person -- and the account
        # it names can read it in their own timeline.
        from types import SimpleNamespace

        account = SimpleNamespace(pk=7, username="player")
        with patch("evennia.server.sessionhandler.SESSIONS") as sessions:
            sessions.session_from_sessid.return_value = SimpleNamespace(account=account)
            with self.assertRaises(NotImplementedError):
                SessionsPanel().watch(_io(), sessid=3, reason="report of harassment")

        self.assertFalse(ConsoleAuditEvent.objects.filter(operation="watch").exists())

    def test_disconnecting_is_recorded(self):
        from types import SimpleNamespace

        account = SimpleNamespace(pk=7, username="player")
        with patch("evennia.server.sessionhandler.SESSIONS") as sessions:
            sessions.session_from_sessid.return_value = SimpleNamespace(account=account)
            SessionsPanel().disconnect(_io(), sessid=3, reason="abusive")
        row = ConsoleAuditEvent.objects.get(panel="sessions", operation="disconnect")
        self.assertEqual(row.message, "abusive")


class TestDegradedMode(TestCase):
    """Which of these survive the game server being down."""

    def test_the_live_ones_need_the_io_owner(self):
        self.assertTrue(ReplPanel.needs_io)
        self.assertTrue(SessionsPanel.needs_io)

    def test_sql_and_server_status_do_not(self):
        # Reading the database and reading process status are both worth having
        # during an outage.
        self.assertFalse(SqlPanel.needs_io)
        self.assertFalse(ServerPanel.needs_io)
