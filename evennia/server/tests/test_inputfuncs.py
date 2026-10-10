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


class TestBotDataInputfunc(unittest.TestCase):
    """Bot frames must tolerate the session/account binding race."""

    def test_unbound_session_drops_frame_with_warning(self):
        session = SimpleNamespace(
            account=None,
            sessid=41,
            protocol_key="discord",
            update_session_counters=mock.Mock(),
        )

        with mock.patch.object(inputfuncs, "log_warn") as log_warn:
            inputfuncs.bot_data_in(session, "payload", type="channel")
            inputfuncs.bot_data_in(session, "second payload", type="channel")

        log_warn.assert_called_once()
        self.assertIn("41", log_warn.call_args.args[0])
        session.update_session_counters.assert_not_called()

    def test_bound_session_dispatches_frame(self):
        account = mock.Mock()
        session = SimpleNamespace(
            account=account,
            sessid=42,
            protocol_key="discord",
            update_session_counters=mock.Mock(),
        )

        inputfuncs.bot_data_in(session, "payload", type="channel")

        account.execute_cmd.assert_called_once_with(
            session=session,
            txt="payload",
            type="channel",
        )
        session.update_session_counters.assert_called_once()


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

    def _hello(self, caps):
        from evennia.server.serversession import ServerSession

        session = ServerSession.__new__(ServerSession)
        session.protocol_flags = {}
        session.sessionhandler = mock.MagicMock()
        session.msg = mock.Mock()
        inputfuncs.azaban_hello(session, caps=caps)
        return session

    def test_an_older_shell_is_asked_to_reload(self):
        session = self._hello({"rendersNodes": True})
        session.msg.assert_called_once()
        self.assertIn("Reload the page", session.msg.call_args.kwargs["text"])

    def test_the_current_shell_is_not(self):
        session = self._hello({"rendersNodes": True, "shell": inputfuncs.SHELL_GENERATION})
        session.msg.assert_not_called()


class TestWebclientOptionsScreenreader(unittest.TestCase):
    """The webclient's screen reader toggle must reach the session, not just the account."""

    def _session(self, saved=None, saved_flags=None):
        account = SimpleNamespace(
            db=SimpleNamespace(
                _saved_webclient_options=saved or {"x": 1},
                _saved_protocol_flags=saved_flags,
            )
        )
        return SimpleNamespace(
            account=account,
            sessid=7,
            protocol_flags={},
            sessionhandler=mock.MagicMock(),
        )

    def test_screenreader_sets_and_syncs_the_session_flag(self):
        session = self._session()
        inputfuncs.webclient_options(session, SCREENREADER=True, cmdid=3)

        self.assertIs(session.protocol_flags["SCREENREADER"], True)
        session.sessionhandler.session_portal_partial_sync.assert_called_once_with(
            {7: {"protocol_flags": {"SCREENREADER": True}}}
        )
        # Still saved as a client preference too.
        self.assertIs(session.account.db._saved_webclient_options["SCREENREADER"], True)

    def test_turning_it_off_clears_the_flag(self):
        session = self._session()
        session.protocol_flags["SCREENREADER"] = True
        inputfuncs.webclient_options(session, SCREENREADER=False)
        self.assertIs(session.protocol_flags["SCREENREADER"], False)

    def test_flag_is_set_before_login(self):
        session = self._session()
        session.account = None
        inputfuncs.webclient_options(session, SCREENREADER=True)
        self.assertIs(session.protocol_flags["SCREENREADER"], True)

    def test_other_options_leave_the_flag_alone(self):
        session = self._session()
        inputfuncs.webclient_options(session, gagprompt=True)
        self.assertNotIn("SCREENREADER", session.protocol_flags)
        session.sessionhandler.session_portal_partial_sync.assert_not_called()
        self.assertIsNone(session.account.db._saved_protocol_flags)

    def test_screenreader_is_saved_account_wide(self):
        session = self._session()
        inputfuncs.webclient_options(session, SCREENREADER=True, cmdid=3)
        self.assertEqual(session.account.db._saved_protocol_flags, {"SCREENREADER": True})

    def test_screenreader_on_keeps_other_saved_flags(self):
        session = self._session(saved_flags={"ANSI": True})
        inputfuncs.webclient_options(session, SCREENREADER=True)
        self.assertEqual(
            session.account.db._saved_protocol_flags, {"ANSI": True, "SCREENREADER": True}
        )

    def test_turning_it_off_pops_the_saved_flag(self):
        session = self._session(saved_flags={"ANSI": True, "SCREENREADER": True})
        inputfuncs.webclient_options(session, SCREENREADER=False)
        self.assertEqual(
            session.account.db._saved_protocol_flags,
            {"ANSI": True},
            "A saved False would restore over a live True flag at login.",
        )

    def test_pre_login_send_does_not_touch_saved_flags(self):
        session = self._session()
        session.account = None
        inputfuncs.webclient_options(session, SCREENREADER=True)
        self.assertIs(session.protocol_flags["SCREENREADER"], True)


class TestShellScreenreaderNotice(unittest.TestCase):
    """A SCREENREADER flag set on the server reaches the web shell."""

    def _session(self, protocol_key):
        from evennia.server.serversession import ServerSession

        session = ServerSession.__new__(ServerSession)
        session.protocol_flags = {}
        session.protocol_key = protocol_key
        session.sessionhandler = mock.MagicMock()
        session.msg = mock.Mock()
        return session

    def test_webclient_hears_the_flag(self):
        session = self._session("websocket")
        session.update_flags(SCREENREADER=True)
        session.msg.assert_called_once_with(screenreader_mode={"on": True})

    def test_telnet_and_other_flags_stay_quiet(self):
        telnet = self._session("telnet")
        telnet.update_flags(SCREENREADER=True)
        telnet.msg.assert_not_called()
        web = self._session("websocket")
        web.update_flags(NOCOLOR=True)
        web.msg.assert_not_called()


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

    def test_undeclared_reply_name_is_refused(self):
        """A client cannot pick a reply name the server would not send."""
        from evennia.scripts.monitorhandler import MONITOR_HANDLER

        with mock.patch.object(MONITOR_HANDLER, "add") as add:
            inputfuncs.monitor(self.session, name="location", outputfunc_name="_internal")
        add.assert_not_called()

    def test_unmonitor_ignores_the_reply_name(self):
        """Removal keys on the field and session, so any reply name stops it."""
        from evennia.scripts.monitorhandler import MONITOR_HANDLER

        with mock.patch.object(MONITOR_HANDLER, "remove") as remove:
            inputfuncs.unmonitor(self.session, name="location", outputfunc_name="_internal")
        remove.assert_called_once()


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


class TestTextLineTerminators(unittest.TestCase):
    """Telnet delivers each line with a trailing newline; cmdhandler must
    never see it, or it survives into ``action.raw_string`` and gets echoed
    back inside nomatch messages. Trailing spaces stay: they are content.
    """

    def _dispatched(self, txt):
        captured = []

        async def capture(*args, **kwargs):
            captured.append(args[1])

        session = mock.MagicMock()
        session.account = None
        with mock.patch.object(inputfuncs, "cmdhandler", side_effect=capture):
            inputfuncs.text(session, txt)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_telnet_newline_is_stripped(self):
        self.assertEqual(self._dispatched("up\n"), "up")

    def test_terminator_free_text_is_unchanged(self):
        self.assertEqual(self._dispatched("up"), "up")

    def test_trailing_spaces_are_kept(self):
        self.assertEqual(self._dispatched("emote waves  \n"), "emote waves  ")


class TestTextDone(unittest.TestCase):
    """``text_done`` answers only after the line's command task has ended."""

    TOKEN = "ab12cd34ef56"

    def setUp(self):
        self.session = mock.MagicMock()
        self.session.account = None

    def test_reply_waits_for_the_command(self):
        import asyncio

        from evennia.utils import clock

        session = self.session
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        saved_loop = (clock._main_loop, clock._loop_thread_id)
        clock.bind_loop(loop)
        seen = []

        async def scenario():
            gate = loop.create_future()

            async def command(sess, txt, **kwargs):
                seen.append((txt, kwargs))
                await gate
                sess.msg("command output")

            with mock.patch.object(inputfuncs, "cmdhandler", side_effect=command):
                inputfuncs.text_done(session, "look", self.TOKEN, cmdobj="injected")
                for _ in range(5):
                    await asyncio.sleep(0)
                self.assertEqual(session.msg.call_args_list, [])
                gate.set_result(None)
                for _ in range(5):
                    await asyncio.sleep(0)

        try:
            loop.run_until_complete(scenario())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
            clock._main_loop, clock._loop_thread_id = saved_loop
        self.assertEqual(seen, [("look", {"callertype": "session", "session": session})])
        self.assertEqual(
            session.msg.call_args_list,
            [mock.call("command output"), mock.call(text_done=self.TOKEN)],
        )

    def test_idle_line_replies_at_once(self):
        with mock.patch.object(inputfuncs, "cmdhandler") as handler:
            inputfuncs.text_done(self.session, "idle", self.TOKEN)
        handler.assert_not_called()
        self.session.msg.assert_called_once_with(text_done=self.TOKEN)

    def test_malformed_input_runs_nothing(self):
        with mock.patch.object(inputfuncs, "text") as text:
            inputfuncs.text_done(self.session, "look")
            inputfuncs.text_done(self.session, "look", "not a token")
            inputfuncs.text_done(self.session, "look", self.TOKEN, "extra")
            inputfuncs.text_done(self.session, ["look"], self.TOKEN)
        text.assert_not_called()
        self.session.msg.assert_not_called()

    def test_over_limit_line_is_refused_like_text(self):
        with (
            mock.patch.object(inputfuncs, "text") as text,
            mock.patch.object(inputfuncs.settings, "MAX_CHAR_LIMIT", 5),
            mock.patch.object(inputfuncs.settings, "MAX_CHAR_LIMIT_WARNING", "too long"),
        ):
            inputfuncs.text_done(self.session, "say a long line", self.TOKEN)
        text.assert_not_called()
        self.assertEqual(
            self.session.msg.call_args_list,
            [mock.call("too long"), mock.call(text_done=self.TOKEN)],
        )


class TestClientOptionsScreenSize(unittest.TestCase):
    """``client_options`` sets a session's screen size, as NAWS does for telnet.

    The webclient reports its terminal's columns and rows this way on connect
    and after each resize. Everything width-aware lays out from the result, so
    it must be a size a table can be drawn at.
    """

    def _session(self):
        return SimpleNamespace(sessid=4, protocol_flags={}, sessionhandler=mock.MagicMock())

    def test_sets_and_syncs_the_size(self):
        session = self._session()
        inputfuncs.client_options(session, screenwidth=96, screenheight="30")
        self.assertEqual(session.protocol_flags["SCREENWIDTH"], {0: 96})
        self.assertEqual(session.protocol_flags["SCREENHEIGHT"], {0: 30})
        session.sessionhandler.session_portal_partial_sync.assert_called_once_with(
            {4: {"protocol_flags": {"SCREENWIDTH": {0: 96}, "SCREENHEIGHT": {0: 30}}}}
        )

    def test_size_is_bounded_like_naws(self):
        # NAWS carries 16-bit sizes, so a telnet client cannot report less
        # than 1 or more than 65535. client_options took any integer, and a
        # zero or negative width reaches EvTable, which raises on it.
        session = self._session()
        inputfuncs.client_options(session, screenwidth=0, screenheight=-4)
        self.assertEqual(session.protocol_flags["SCREENWIDTH"], {0: 1})
        self.assertEqual(session.protocol_flags["SCREENHEIGHT"], {0: 1})
        inputfuncs.client_options(session, screenwidth=10**9, screenheight=10**9)
        self.assertEqual(session.protocol_flags["SCREENWIDTH"], {0: 65535})
        self.assertEqual(session.protocol_flags["SCREENHEIGHT"], {0: 65535})
