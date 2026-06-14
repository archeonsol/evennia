"""
Tests for server input functions.
"""

import pickle
import unittest
from types import SimpleNamespace
from unittest import mock

import evennia
from evennia.server import inputfuncs
from evennia.utils.test_resources import BaseEvenniaTest


class TestLoginInputfunc(unittest.TestCase):
    """The web/REST ``login`` inputfunc routes through the engine login op."""

    def test_routes_to_engine_login_session(self):
        from evennia.actions.default import unloggedin

        session = SimpleNamespace(logged_in=False)
        with mock.patch.object(unloggedin, "login_session") as login_session:
            inputfuncs.login(session, name="bob", password="hunter2")
        login_session.assert_called_once_with(session, "bob", "hunter2")

    def test_noop_when_already_logged_in(self):
        from evennia.actions.default import unloggedin

        session = SimpleNamespace(logged_in=True)
        with mock.patch.object(unloggedin, "login_session") as login_session:
            inputfuncs.login(session, name="bob", password="hunter2")
        login_session.assert_not_called()


class TestMonitoredInputfunc(BaseEvenniaTest):
    """
    Regressions for monitor/monitored inputfunc handling.
    """

    def test_monitored_payload_is_pickleable(self):
        """
        The monitored payload sent over AMP must not include raw Session objects.
        """

        # the fixture session already puppets char1 (auto-puppet on login)
        inputfuncs.monitor(self.session, name="location")
        inputfuncs.monitored(self.session)

        sent_session = evennia.SESSION_HANDLER.data_out.call_args.args[0]
        sent_kwargs = evennia.SESSION_HANDLER.data_out.call_args.kwargs
        monitors = sent_kwargs["monitored"][0]
        monitor_kwargs = monitors[0][4]

        self.assertEqual(sent_session, self.session)
        self.assertIn("session", monitor_kwargs)
        self.assertIsInstance(monitor_kwargs["session"], str)
        pickle.dumps((self.session.sessid, sent_kwargs), pickle.HIGHEST_PROTOCOL)


class TestTextDispatchErrback(unittest.TestCase):
    """``text`` must consume a failed cmdhandler Deferred by logging it, not
    leave it for Twisted's garbage collector to maybe-report."""

    def test_failed_dispatch_deferred_is_logged(self):
        from unittest import mock

        from twisted.internet import defer

        session = mock.MagicMock()
        session.account = None
        failing = defer.fail(RuntimeError("dispatch kaboom"))
        with (
            mock.patch.object(inputfuncs, "cmdhandler", return_value=failing),
            mock.patch.object(inputfuncs, "log_err") as log_mock,
        ):
            inputfuncs.text(session, "look")
        log_mock.assert_called()
        logged = " ".join(str(a) for c in log_mock.call_args_list for a in c.args)
        self.assertIn("dispatch kaboom", logged)
