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


class TestAzabanHelloInputfunc(unittest.TestCase):
    """The Azaban handshake must survive Server/Portal session resync."""

    def test_capabilities_are_synchronized_to_the_portal(self):
        """Capability flags use the session synchronization API, not local mutation."""
        from evennia.narrative.rendernode import CLIENT_NARRATIVE_FLAG
        from evennia.server.serversession import ServerSession

        session = ServerSession.__new__(ServerSession)
        session.protocol_flags = {}
        session.sessionhandler = mock.MagicMock()

        synchronized = inputfuncs.azaban_hello(
            session,
            caps={"rendersNodes": True, "patches": True, "unknown": "preserved"},
        )

        self.assertTrue(synchronized)
        self.assertTrue(session.protocol_flags[CLIENT_NARRATIVE_FLAG])
        self.assertEqual(
            session.protocol_flags["AZABAN_CAPS"],
            {
                "rendersNodes": True,
                "patches": True,
                "unknown": "preserved",
            },
        )
        session.sessionhandler.session_portal_sync.assert_called_once_with(session)


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
    """``text`` must consume a failed cmdhandler coroutine by logging it, not
    leave the task exception unhandled.

    ``cmdhandler`` is now ``async``; ``text`` kicks it off via
    ``clock.run_coroutine``, whose done-callback logs unhandled exceptions
    through ``evennia.utils.logger.log_err``. With no running loop (this test
    harness) the coroutine is driven synchronously and the same backstop fires.
    """

    def test_failed_dispatch_coroutine_is_logged(self):
        from evennia.utils import logger

        async def failing(*args, **kwargs):
            raise RuntimeError("dispatch kaboom")

        session = mock.MagicMock()
        session.account = None
        with (
            mock.patch.object(inputfuncs, "cmdhandler", side_effect=failing),
            mock.patch.object(logger, "log_err") as log_mock,
        ):
            inputfuncs.text(session, "look")
        log_mock.assert_called()
        logged = " ".join(str(a) for c in log_mock.call_args_list for a in c.args)
        self.assertIn("dispatch kaboom", logged)
