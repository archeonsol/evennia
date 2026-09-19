"""
Outbound-buffer behaviour of ``ServerSessionHandler`` around disconnect.

``data_out`` defers its AMP send to the next reactor iteration, so a caller that
sends and then disconnects in the same iteration leaves messages in the buffer.
Those must reach the Portal, not be dropped: the webclient's quit menu is raised
by a ``logout`` OOB sent immediately before the session is torn down.
"""

from unittest import TestCase
from unittest.mock import Mock, patch

from evennia.server.sessionhandler import ServerSessionHandler


def _session(sessid=1):
    """A session stub with only what ``disconnect`` touches.

    ``account`` is None so the logout-logging branch (and its account signal)
    stays out of the way; the buffer handling under test is the same either way.
    """
    return Mock(sessid=sessid, account=None, address="testaddress")


class TestDisconnectFlushesOutbuf(TestCase):
    """Pending output must be flushed by disconnect, never discarded."""

    def setUp(self):
        self.handler = ServerSessionHandler()
        self.session = _session()
        self.handler[self.session.sessid] = self.session
        patcher = patch("evennia.server.sessionhandler._send_admin_to_portal")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_pending_output_is_flushed(self):
        self.handler._outbuf[1] = [{"logout": ("quit",)}]
        with patch.object(self.handler, "_flush_outbuf") as flush:
            self.handler.disconnect(self.session)
        flush.assert_called_once_with(1)

    def test_nothing_pending_means_no_flush(self):
        with patch.object(self.handler, "_flush_outbuf") as flush:
            self.handler.disconnect(self.session)
        flush.assert_not_called()

    def test_buffer_is_empty_afterwards(self):
        self.handler._outbuf[1] = [{"logout": ("quit",)}]
        with patch("evennia.server.sessionhandler.evennia") as mock_evennia:
            self.handler.disconnect(self.session)
        self.assertNotIn(1, self.handler._outbuf)
        bus = mock_evennia.EVENNIA_SERVER_SERVICE.portal_bus
        self.assertTrue(bus.send_MsgServer2Portal.called)


class TestLoginVeto(TestCase):
    """A refused login must tell the resumable shell, not just close the socket.

    Without the ``logout`` OOB the webclient reads the close as a drop and
    reconnects, re-hitting the veto forever; the send goes to the vetoing
    session alone so the account's other live sessions are untouched.
    """

    def test_veto_sends_logout_before_disconnecting(self):
        handler = ServerSessionHandler()
        manager = Mock()
        session = Mock(sessid=1, logged_in=False)
        session.msg.side_effect = lambda **kw: manager.msg(**kw)
        account = Mock()
        account.at_pre_login.return_value = False
        handler[1] = session
        with patch.object(
            handler,
            "disconnect",
            side_effect=lambda *a, **kw: manager.disconnect(*a, **kw),
        ):
            handler.login(session, account, testmode=True)
        # The frame must ship while the session can still be flushed.
        names = [c[0] for c in manager.mock_calls if c[0]]
        self.assertEqual(names, ["msg", "disconnect"])
        manager.msg.assert_called_once_with(logout=("login refused",))
        manager.disconnect.assert_called_once_with(session, reason="Login refused.")


class TestOutputOrder(TestCase):
    """Coalescing preserves order and the options of each text run."""

    def _flush(self, messages):
        """Return frames emitted by the real buffer drain."""
        handler = ServerSessionHandler()
        session = _session()
        handler[1] = session
        handler._outbuf[1] = messages
        with (
            patch("evennia.server.sessionhandler.evennia") as engine,
            patch.object(
                handler, "clean_senddata", side_effect=lambda session, frame: frame
            ),
        ):
            handler._flush_outbuf(1)
        return [
            call.kwargs
            for call in engine.EVENNIA_SERVER_SERVICE.portal_bus.send_MsgServer2Portal.call_args_list
        ]

    def test_text_runs_stay_between_standalone_frames(self):
        """Narrative and OOB must not be overtaken by later text."""
        frames = self._flush(
            [
                {"narrative": {"body": "first"}},
                {"text": "second"},
                {"text": "third"},
                {"channel_msg": "fourth"},
                {"text": "fifth"},
            ]
        )
        self.assertEqual(
            frames,
            [
                {"narrative": {"body": "first"}},
                {"text": "second\nthird"},
                {"channel_msg": "fourth"},
                {"text": "fifth"},
            ],
        )

    def test_different_text_options_are_not_merged(self):
        """Prompt styling cannot bleed into a following plain message."""
        self.assertEqual(
            self._flush(
                [
                    {"text": ("one", {"type": "notice"})},
                    {"text": ("two", {"type": "notice"})},
                    {"text": "three"},
                ]
            ),
            [
                {"text": ("one\ntwo", {"type": "notice"})},
                {"text": "three"},
            ],
        )


class TestGroupedOutput(TestCase):
    """Final byte-identical frames share one Server-to-Portal publication."""

    def setUp(self):
        self.handler = ServerSessionHandler()
        self.sessions = [_session(1), _session(2)]
        for session in self.sessions:
            self.handler[session.sessid] = session

    def test_one_flush_is_scheduled_for_the_reactor_turn(self):
        with patch("evennia.utils.clock.call_later") as call_later:
            for session in self.sessions:
                self.handler.data_out(session, text="same")
        call_later.assert_called_once_with(0, self.handler._flush_all_outbuf)

    def test_identical_clean_frames_are_multicast(self):
        for session in self.sessions:
            self.handler._outbuf[session.sessid] = [{"text": "same"}]
        normalized = {"text": [["same"], {}]}
        with (
            patch("evennia.server.sessionhandler.evennia") as engine,
            patch.object(self.handler, "clean_senddata", return_value=normalized),
        ):
            self.handler._flush_all_outbuf()
        bus = engine.EVENNIA_SERVER_SERVICE.portal_bus
        bus.send_MsgServer2PortalMany.assert_called_once_with([1, 2], **normalized)
        bus.send_MsgServer2Portal.assert_not_called()

    def test_grouping_happens_after_session_specific_cleaning(self):
        for session in self.sessions:
            self.handler._outbuf[session.sessid] = [{"text": "source"}]

        def clean(session, _frame):
            return {"text": [[f"for {session.sessid}"], {}]}

        with (
            patch("evennia.server.sessionhandler.evennia") as engine,
            patch.object(self.handler, "clean_senddata", side_effect=clean),
        ):
            self.handler._flush_all_outbuf()
        bus = engine.EVENNIA_SERVER_SERVICE.portal_bus
        self.assertEqual(bus.send_MsgServer2Portal.call_count, 2)
        bus.send_MsgServer2PortalMany.assert_not_called()

    def test_multicast_rounds_preserve_per_session_order(self):
        for session in self.sessions:
            self.handler._outbuf[session.sessid] = [
                {"text": "first"},
                {"text": ("second", {"type": "notice"})},
            ]
        with (
            patch("evennia.server.sessionhandler.evennia") as engine,
            patch.object(
                self.handler,
                "clean_senddata",
                side_effect=lambda _session, frame: frame,
            ),
        ):
            self.handler._flush_all_outbuf()
        calls = (
            engine.EVENNIA_SERVER_SERVICE.portal_bus.send_MsgServer2PortalMany.call_args_list
        )
        self.assertEqual(
            [call.kwargs["text"] for call in calls],
            ["first", ("second", {"type": "notice"})],
        )

    def test_one_broken_session_does_not_strand_the_rest(self):
        for session in self.sessions:
            self.handler._outbuf[session.sessid] = [{"text": "source"}]

        def clean(session, frame):
            if session.sessid == 1:
                raise ValueError("broken protocol transform")
            return frame

        with (
            patch("evennia.server.sessionhandler.evennia") as engine,
            patch("evennia.server.sessionhandler.log_trace"),
            patch.object(self.handler, "clean_senddata", side_effect=clean),
        ):
            self.handler._flush_all_outbuf()
        bus = engine.EVENNIA_SERVER_SERVICE.portal_bus
        bus.send_MsgServer2Portal.assert_called_once_with(
            self.sessions[1], text="source"
        )
