try:
    from django.utils.unittest import TestCase
except ImportError:
    from django.test import TestCase

try:
    from django.utils import unittest
except ImportError:
    import unittest

import json
import string
import sys

import mock
from mock import MagicMock, Mock
from twisted.conch.telnet import DO, DONT, IAC, NAWS, SB, SE, WILL
from twisted.internet.base import DelayedCall
from twisted.test import proto_helpers
from twisted.trial.unittest import TestCase as TwistedTestCase

import evennia
from evennia.server.portal import irc, portalsessionhandler
from evennia.server.portal.portalsessionhandler import PortalSessionHandler
from evennia.server.portal.service import EvenniaPortalService
from evennia.server.portal.ws_protocol import WSServerFactory
from evennia.utils.test_resources import BaseEvenniaTest

from .amp import AMP_MAXLEN, AMPMultiConnectionProtocol, MsgPortal2Server, MsgServer2Portal
from .amp_server import AMPServerFactory
from .mccp import MCCP
from .mssp import MSSP
from .mxp import MXP
from .naws import DEFAULT_HEIGHT, DEFAULT_WIDTH
from .suppress_ga import SUPPRESS_GA
from .telnet import TelnetProtocol, TelnetServerFactory
from .telnet_oob import MSDP, MSDP_VAL, MSDP_VAR
from .ttype import IS, TTYPE
from .webclient import WebSocketClient


class TestAMPServer(TwistedTestCase):
    """
    Test AMP communication
    """

    def setUp(self):
        super().setUp()
        portal = Mock()
        factory = AMPServerFactory(portal)
        self.proto = factory.buildProtocol(("localhost", 0))
        self.transport = MagicMock()  # proto_helpers.StringTransport()
        self.transport.client = ["localhost"]
        self.transport.write = MagicMock()

    def test_amp_out(self):
        # MsgServer2Portal uses the session-serde JSON envelope (see
        # amp.dumps_session / amp_serde.pack_session_message), not pickle.
        # Asserting exact wire bytes would be both pickle-version fragile and
        # wrong for the JSON path; instead verify the transport got the AMP
        # frame for the right command and that dumps_session round-trips the
        # payload through loads_session.
        from evennia.server.portal import amp

        self.proto.makeConnection(self.transport)
        self.proto.data_to_server(MsgServer2Portal, 1, test=2)

        self.assertTrue(self.transport.write.called)
        wire = self.transport.write.call_args[0][0]
        self.assertIn(b"MsgServer2Portal", wire)
        self.assertIn(b"packed_data", wire)

        packed = amp.dumps_session((1, {"test": 2}))
        sessid, kwargs = amp.loads_session(packed)
        self.assertEqual(sessid, 1)
        self.assertEqual(kwargs, {"test": 2})

        with mock.patch("evennia.server.portal.amp.amp.AMP.dataReceived") as mocked_amprecv:
            self.proto.dataReceived(wire)
            mocked_amprecv.assert_called_with(wire)

    def test_amp_in(self):
        # MsgPortal2Server uses the session-serde JSON envelope (see
        # amp.dumps_session / amp_serde.pack_session_message), not pickle.
        # Asserting the exact wire bytes would be both pickle-version
        # fragile and wrong for the JSON path; instead verify that the
        # transport got the AMP frame for the right command and that
        # dumps_session round-trips the payload through loads_session.
        from evennia.server.portal import amp

        self.proto.makeConnection(self.transport)
        self.proto.data_to_server(MsgPortal2Server, 1, test=2)

        self.assertTrue(self.transport.write.called)
        wire = self.transport.write.call_args[0][0]
        self.assertIn(b"MsgPortal2Server", wire)
        self.assertIn(b"packed_data", wire)

        packed = amp.dumps_session((1, {"test": 2}))
        sessid, kwargs = amp.loads_session(packed)
        self.assertEqual(sessid, 1)
        self.assertEqual(kwargs, {"test": 2})

        with mock.patch("evennia.server.portal.amp.amp.AMP.dataReceived") as mocked_amprecv:
            self.proto.dataReceived(wire)
            mocked_amprecv.assert_called_with(wire)

    def test_large_msg(self):
        """
        Send a message whose payload exceeds AMP_MAXLEN. The serde packs our own
        trusted data without a string-length cap, and AMP's Compressed argument
        splits the oversized value across continuation frames (``packed_data``,
        ``packed_data.2``, ...) on the wire.
        """
        self.proto.makeConnection(self.transport)
        outstr = "test" * AMP_MAXLEN
        self.proto.data_to_server(MsgServer2Portal, 1, test=outstr)

        self.assertTrue(self.transport.write.called)
        wire = b"".join(call.args[0] for call in self.transport.write.call_args_list)
        self.assertIn(b"MsgServer2Portal", wire)
        # the oversized value forced AMP to emit at least one continuation frame
        self.assertIn(b"packed_data.2", wire)

        # the serde itself round-trips an oversized outbound payload on pack
        from evennia.server.portal import amp

        packed = amp.dumps_session((1, {"test": outstr}))
        self.assertTrue(packed.startswith(b"J1"))


class TestIRC(TestCase):
    def test_plain_ansi(self):
        """
        Test that printable characters do not get mangled.
        """
        irc_ansi = irc.parse_ansi_to_irc(string.printable)
        ansi_irc = irc.parse_irc_to_ansi(string.printable)
        self.assertEqual(irc_ansi, string.printable)
        self.assertEqual(ansi_irc, string.printable)

    def test_bold(self):
        s_irc = "\x02thisisatest"
        s_eve = r"|hthisisatest"
        self.assertEqual(irc.parse_ansi_to_irc(s_eve), s_irc)
        self.assertEqual(s_eve, irc.parse_irc_to_ansi(s_irc))

    def test_italic(self):
        s_irc = "\x02thisisatest"
        s_eve = r"|hthisisatest"
        self.assertEqual(irc.parse_ansi_to_irc(s_eve), s_irc)

    def test_colors(self):
        color_map = (
            ("\0030", r"|w"),
            ("\0031", r"|X"),
            ("\0032", r"|B"),
            ("\0033", r"|G"),
            ("\0034", r"|r"),
            ("\0035", r"|R"),
            ("\0036", r"|M"),
            ("\0037", r"|Y"),
            ("\0038", r"|y"),
            ("\0039", r"|g"),
            ("\00310", r"|C"),
            ("\00311", r"|c"),
            ("\00312", r"|b"),
            ("\00313", r"|m"),
            ("\00314", r"|x"),
            ("\00315", r"|W"),
            ("\00399,5", r"|[r"),
            ("\00399,3", r"|[g"),
            ("\00399,7", r"|[y"),
            ("\00399,2", r"|[b"),
            ("\00399,6", r"|[m"),
            ("\00399,10", r"|[c"),
            ("\00399,15", r"|[w"),
            ("\00399,1", r"|[x"),
        )

        for m in color_map:
            self.assertEqual(irc.parse_irc_to_ansi(m[0]), m[1])
            self.assertEqual(m[0], irc.parse_ansi_to_irc(m[1]))

    def test_identity(self):
        """
        Test that the composition of the function and
        its inverse gives the correct string.
        """

        s = r"|wthis|Xis|gis|Ma|C|complex|*string"

        self.assertEqual(irc.parse_irc_to_ansi(irc.parse_ansi_to_irc(s)), s)


class TestTelnet(TwistedTestCase):
    def setUp(self):
        super().setUp()
        # connectionMade schedules the telnet handshake via delay(); without a
        # bound loop _DeferLaterCompat.call_later has nowhere to schedule and
        # raises. Bind a fresh, non-running loop (mirroring the old always-present
        # reactor): the handshake is scheduled but never fires, and each test
        # cancels it. Restore the prior global loop identity on teardown.
        import asyncio

        from evennia.utils import clock

        self._saved_loop = (clock._main_loop, clock._loop_thread_id)
        self._loop = asyncio.new_event_loop()
        clock.bind_loop(self._loop)

        def _restore():
            self._loop.close()
            clock._main_loop, clock._loop_thread_id = self._saved_loop

        self.addCleanup(_restore)
        self.portal = EvenniaPortalService()
        evennia.EVENNIA_PORTAL_SERVICE = self.portal
        self.amp_server_factory = AMPServerFactory(self.portal)
        self.amp_server = self.amp_server_factory.buildProtocol("127.0.0.1")
        factory = TelnetServerFactory()
        factory.protocol = TelnetProtocol
        evennia.PORTAL_SESSION_HANDLER = PortalSessionHandler()
        factory.sessionhandler = evennia.PORTAL_SESSION_HANDLER
        factory.sessionhandler.portal = Mock()
        self.proto = factory.buildProtocol(("localhost", 0))
        self.transport = proto_helpers.StringTransport()
        self.addCleanup(factory.sessionhandler.disconnect_all)

    @mock.patch.object(portalsessionhandler, "clock", new=MagicMock())
    def test_command_stacking_no_type_error(self):
        self.transport.client = ["localhost"]
        self.transport.setTcpKeepAlive = Mock()
        d = self.proto.makeConnection(self.transport)
        # Mudlet sends multiple commands in one packet when command stacking
        data = b"wave\r\nsay hi\r\n"
        try:
            self.proto.dataReceived(data)
        except TypeError:
            self.fail("dataReceived raised TypeError on stacked commands")
        # clean up to prevent Unclean reactor
        self.proto.nop_keep_alive.stop()
        self.proto._handshake_delay.cancel()
        return d

    @mock.patch.object(portalsessionhandler, "clock", new=MagicMock())
    def test_mudlet_ttype(self):
        self.transport.client = ["localhost"]
        self.transport.setTcpKeepAlive = Mock()
        d = self.proto.makeConnection(self.transport)
        # test suppress_ga
        self.assertTrue(self.proto.protocol_flags["NOGOAHEAD"])
        self.proto.dataReceived(IAC + DONT + SUPPRESS_GA)
        self.assertFalse(self.proto.protocol_flags["NOGOAHEAD"])
        self.assertEqual(self.proto.handshakes, 7)
        # test naws
        self.assertEqual(self.proto.protocol_flags["SCREENWIDTH"], {0: DEFAULT_WIDTH})
        self.assertEqual(self.proto.protocol_flags["SCREENHEIGHT"], {0: DEFAULT_HEIGHT})
        self.proto.dataReceived(IAC + WILL + NAWS)
        self.proto.dataReceived(b"".join([IAC, SB, NAWS, b"", b"x", b"", b"d", IAC, SE]))
        self.assertEqual(self.proto.protocol_flags["SCREENWIDTH"][0], 78)
        self.assertEqual(self.proto.protocol_flags["SCREENHEIGHT"][0], 45)
        self.assertEqual(self.proto.handshakes, 6)
        # test ttype
        self.assertFalse(self.proto.protocol_flags["TTYPE"])
        self.assertTrue(self.proto.protocol_flags["ANSI"])
        self.proto.dataReceived(IAC + WILL + TTYPE)
        self.proto.dataReceived(b"".join([IAC, SB, TTYPE, IS, b"MUDLET", IAC, SE]))
        self.assertTrue(self.proto.protocol_flags["XTERM256"])
        self.assertEqual(self.proto.protocol_flags["CLIENTNAME"], "MUDLET")
        self.assertTrue(self.proto.protocol_flags["FORCEDENDLINE"])
        self.assertTrue(self.proto.protocol_flags["NOGOAHEAD"])
        self.assertFalse(self.proto.protocol_flags["NOPROMPTGOAHEAD"])
        self.proto.dataReceived(b"".join([IAC, SB, TTYPE, IS, b"XTERM", IAC, SE]))
        self.proto.dataReceived(b"".join([IAC, SB, TTYPE, IS, b"MTTS 137", IAC, SE]))
        self.assertEqual(self.proto.handshakes, 5)
        # test mccp
        self.proto.dataReceived(IAC + DONT + MCCP)
        self.assertFalse(self.proto.protocol_flags["MCCP"])
        self.assertEqual(self.proto.handshakes, 4)
        # test mssp
        self.proto.dataReceived(IAC + DONT + MSSP)
        self.assertEqual(self.proto.handshakes, 3)
        # test oob
        self.proto.dataReceived(IAC + DO + MSDP)
        self.proto.dataReceived(
            b"".join([IAC, SB, MSDP, MSDP_VAR, b"LIST", MSDP_VAL, b"COMMANDS", IAC, SE])
        )
        self.assertTrue(self.proto.protocol_flags["OOB"])
        self.assertEqual(self.proto.handshakes, 2)
        # test mxp
        self.proto.dataReceived(IAC + DONT + MXP)
        self.assertFalse(self.proto.protocol_flags["MXP"])
        self.assertEqual(self.proto.handshakes, 1)
        # clean up to prevent Unclean reactor
        self.proto.nop_keep_alive.stop()
        self.proto._handshake_delay.cancel()
        return d

    def test_mxp_parse(self):
        """
        Test that mxp_parse correctly converts Evennia MXP markup to MXP escape sequences,
        and leaves messages without MXP markup untouched.
        """
        from evennia.server.portal.mxp import MXP_TEMPSECURE, mxp_parse

        # no MXP markup - should be returned unchanged
        self.assertEqual(mxp_parse("hello world"), "hello world")

        # angle brackets without MXP markup - should be returned unchanged
        self.assertEqual(mxp_parse("<name>"), "<name>")

        # basic link substitution
        result = mxp_parse("|lchelp overview|lthelp overview|le")
        self.assertIn('<SEND HREF="help overview">', result)
        self.assertIn("help overview", result)
        self.assertIn(MXP_TEMPSECURE, result)
        self.assertNotIn("|lc", result)
        self.assertNotIn("|lt", result)
        self.assertNotIn("|le", result)

        # surrounding text should pass through unchanged
        result = mxp_parse("<|lchelp eat|lthelp eat|le>")
        self.assertIn("<", result)
        self.assertIn(">", result)
        self.assertNotIn("&lt;", result)
        self.assertNotIn("&gt;", result)
        self.assertIn('<SEND HREF="help eat">', result)

        # non-MXP ampersands should pass through unchanged
        result = mxp_parse("fish & chips |lchelp eat|lthelp eat|le")
        self.assertIn("fish & chips", result)
        self.assertNotIn("&amp;", result)

    @mock.patch.object(portalsessionhandler, "clock", new=MagicMock())
    def test_naws_resize_syncs_updated_width(self):
        """
        Verify that a NAWS resize packet causes sessionhandler.sync to be called
        AFTER negotiate_sizes has updated SCREENWIDTH, not before.
        Regression test for the ordering bug introduced in #3498.
        """

        self.transport.client = ["localhost"]
        self.transport.setTcpKeepAlive = Mock()
        d = self.proto.makeConnection(self.transport)
        self.addCleanup(self.proto.nop_keep_alive.stop)
        self.addCleanup(self.proto._handshake_delay.cancel)

        # Complete NAWS handshake: client says WILL NAWS -> sets AUTORESIZE=True
        self.proto.dataReceived(IAC + WILL + NAWS)
        self.assertTrue(self.proto.protocol_flags["AUTORESIZE"])

        # Patch sync before any NAWS subneg so it never tries sessionhandler.get()
        # (the session isn't reachable via get() in this test setup). Capture
        # SCREENWIDTH at the moment sync is called to assert ordering.
        synced_widths = []

        def capturing_sync(session):
            synced_widths.append(self.proto.protocol_flags["SCREENWIDTH"][0])

        self.proto.sessionhandler.sync = capturing_sync

        # Initial size from handshake (120 wide, 40 tall)
        self.proto.dataReceived(b"".join([IAC, SB, NAWS, b"\x00\x78", b"\x00\x28", IAC, SE]))
        self.assertEqual(self.proto.protocol_flags["SCREENWIDTH"][0], 120)

        synced_widths.clear()

        # Simulate a terminal resize to 160 wide, 50 tall
        self.proto.dataReceived(b"".join([IAC, SB, NAWS, b"\x00\xa0", b"\x00\x32", IAC, SE]))

        # SCREENWIDTH must be updated to 160 BEFORE sync fires
        self.assertEqual(self.proto.protocol_flags["SCREENWIDTH"][0], 160)
        self.assertEqual(synced_widths, [160])  # sync saw the NEW width, not 120

        return d


class TestWebSocket(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        self.portal = EvenniaPortalService()
        evennia.EVENNIA_PORTAL_SERVICE = self.portal
        self.amp_server_factory = AMPServerFactory(self.portal)
        self.amp_server = self.amp_server_factory.buildProtocol("127.0.0.1")
        self.proto = WebSocketClient()
        self.proto.factory = WSServerFactory()
        evennia.PORTAL_SESSION_HANDLER = PortalSessionHandler()
        self.proto.factory.sessionhandler = evennia.PORTAL_SESSION_HANDLER
        self.proto.sessionhandler = evennia.PORTAL_SESSION_HANDLER
        self.proto.sessionhandler.portal = Mock()
        self.proto.transport = proto_helpers.StringTransport()
        # self.proto.transport = proto_helpers.FakeDatagramTransport()
        self.proto.transport.client = ["localhost"]
        self.proto.transport.setTcpKeepAlive = Mock()
        self.proto.state = MagicMock()
        self.addCleanup(self.proto.factory.sessionhandler.disconnect_all)
        DelayedCall.debug = True

    def tearDown(self):
        super().tearDown()

    @mock.patch.object(portalsessionhandler, "clock", new=MagicMock())
    def test_data_in(self):
        self.proto.sessionhandler.data_in = MagicMock()
        self.proto.onOpen()
        msg = json.dumps(["logged_in", (), {}]).encode()
        self.proto.onMessage(msg, isBinary=False)
        self.proto.sessionhandler.data_in.assert_called_with(self.proto, logged_in=[[], {}])
        sendStr = "You can get anything you want at Alice's Restaurant."
        msg = json.dumps(["text", (sendStr,), {}]).encode()
        self.proto.onMessage(msg, isBinary=False)
        self.proto.sessionhandler.data_in.assert_called_with(self.proto, text=[[sendStr], {}])

    @mock.patch.object(portalsessionhandler, "clock", new=MagicMock())
    def test_data_out(self):
        self.proto.onOpen()
        self.proto.sendEncoded = MagicMock()
        self.proto.sessionhandler.data_out(self.proto, text=[["Excepting Alice"], {}])
        self.proto.sendEncoded.assert_called_once()
        call_args = self.proto.sendEncoded.call_args
        data = call_args[0][0]
        # EvenniaV1Format encodes as JSON TEXT frame
        parsed = json.loads(data)
        self.assertEqual(parsed[0], "text")
        self.assertEqual(parsed[1], ["Excepting Alice"])
        # Verify frame is sent as TEXT (not BINARY) — v1 uses JSON TEXT frames
        args, kwargs = call_args
        is_binary = kwargs.get("is_binary", args[1] if len(args) > 1 else False)
        self.assertFalse(is_binary)


class TestServerWatchdog(TestCase):
    """Portal dead-server watchdog (``_maybe_restart_dead_server``)."""

    def _armed_portal(self):
        """A Portal in the armed state: a Server was up and its restart mode cleared."""
        portal = EvenniaPortalService()
        portal.server_restart_mode = None
        portal.server_process_id = 4242
        portal.server_twistd_cmd = ["python", "server.py"]
        portal._launcher_amp_protocol = MagicMock()
        portal._last_server_autorestart = 0.0
        return portal

    @mock.patch("evennia.server.portal.service.logger")
    @mock.patch("evennia.server.portal.service.time")
    @mock.patch("evennia.server.redis_bus._pid_alive", return_value=False)
    def test_failed_restart_does_not_permanently_disarm(self, _alive, mock_time, _logger):
        portal = self._armed_portal()

        # first tick sees the Server dead and fires a restart
        mock_time.monotonic.return_value = 1000.0
        portal._maybe_restart_dead_server()
        self.assertEqual(portal._launcher_amp_protocol.start_server.call_count, 1)

        # the relaunched Server never comes up (no PSYNC → no new pid reported).
        # once past the 30s throttle a later tick must try again, not stay disarmed.
        mock_time.monotonic.return_value = 1031.0
        portal._maybe_restart_dead_server()
        self.assertEqual(portal._launcher_amp_protocol.start_server.call_count, 2)


class TestDiscordHeartbeatCancel(TestCase):
    """Discord heartbeat timer cancel (``_cancel_heartbeat``)."""

    def test_cancel_heartbeat_cancels_asyncio_timerhandle(self):
        import asyncio

        from evennia.server.portal.discord import DiscordClient

        loop = asyncio.new_event_loop()
        try:
            fired = []
            handle = loop.call_later(1000, lambda: fired.append(True))
            client = object.__new__(DiscordClient)
            client.nextHeartbeatCall = handle

            client._cancel_heartbeat()

            # the underlying asyncio timer must actually be cancelled, not just
            # the local ref dropped, or a stale heartbeat keeps firing
            self.assertTrue(handle.cancelled())
            self.assertIsNone(client.nextHeartbeatCall)
        finally:
            loop.close()


class TestDiscordThreadRetry(TestCase):
    """Forum-thread create retry must not lose the popped ``forum`` kwarg."""

    def test_retry_preserves_forum_kwarg(self):
        from evennia.server.portal import discord

        client = object.__new__(discord.DiscordClient)
        client.sessionhandler = MagicMock()
        captured = {}

        class _Req:
            def addCallback(self, cb):
                captured["cb"] = cb
                return self

        with (
            mock.patch.object(discord.http, "request", return_value=_Req()),
            mock.patch.object(discord, "delay") as mock_delay,
        ):
            client.send_create_thread("MyThread", "123", "job7", forum=True, applied_tags=["9"])
            resp = MagicMock()
            resp.code = 503  # retryable
            captured["cb"](resp)

        mock_delay.assert_called_once()
        _args, kwargs = mock_delay.call_args
        self.assertTrue(kwargs.get("forum"), "retry must preserve the forum flag")


class TestWebclientGoingAwayAuth(TestCase):
    """GOING_AWAY (tab close) must clear the auto-login stamp, except on reboot."""

    class _CSession(dict):
        def save(self):
            self.saved = True

    def _client(self, disconnect_all=False):
        from evennia.server.portal.webclient import WebSocketClient

        client = object.__new__(WebSocketClient)
        client.nonce = 7
        client.resume_token = None
        client.out_buffer = []
        client.sessionhandler = MagicMock()
        client.sessionhandler._disconnect_all = disconnect_all
        client.sendClose = MagicMock()
        return client

    def test_tab_close_clears_auto_login_stamp(self):
        from evennia.server.portal.webclient import GOING_AWAY

        client = self._client(disconnect_all=False)
        csession = self._CSession(webclient_authenticated_uid=42, webclient_authenticated_nonce=7)
        client.get_client_session = lambda: csession

        client.onClose(False, code=GOING_AWAY)

        self.assertIsNone(csession["webclient_authenticated_uid"])
        self.assertEqual(csession["webclient_authenticated_nonce"], 0)

    def test_reboot_preserves_auto_login_stamp(self):
        from evennia.server.portal.webclient import GOING_AWAY

        client = self._client(disconnect_all=True)
        csession = self._CSession(webclient_authenticated_uid=42, webclient_authenticated_nonce=7)
        client.get_client_session = lambda: csession

        client.onClose(False, code=GOING_AWAY)

        self.assertEqual(csession["webclient_authenticated_uid"], 42)


class TestAsyncioReconnectHardening(TestCase):
    """Reconnect loops must not retry forever on non-transient errors."""

    def _factory(self):
        f = MagicMock()
        f.stopping = False
        f.bot = None
        f.ssl = False
        f.initialDelay = 0.01
        f.factor = 1.0
        f.maxDelay = 0.01
        return f

    def test_irc_reconnect_breaks_on_non_transient_error(self):
        import asyncio

        from evennia.server.portal import irc

        factory = self._factory()
        loop = asyncio.new_event_loop()
        calls = []

        async def boom(*a, **k):
            calls.append(1)
            raise AttributeError("programming error, not a network blip")

        try:
            asyncio.set_event_loop(loop)
            with mock.patch.object(loop, "create_connection", side_effect=boom):
                loop.run_until_complete(
                    asyncio.wait_for(irc.connect_irc_asyncio(factory), timeout=1)
                )
            # non-transient → exactly one attempt, then break (no infinite retry)
            self.assertEqual(len(calls), 1)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ws_reconnect_breaks_on_non_transient_error(self):
        import asyncio

        from evennia.server.portal import ws_protocol

        factory = self._factory()
        factory.ws_url = "ws://localhost:1/"
        loop = asyncio.new_event_loop()
        calls = []

        async def boom(*a, **k):
            calls.append(1)
            raise AttributeError("programming error, not a network blip")

        try:
            asyncio.set_event_loop(loop)
            with mock.patch.object(loop, "create_connection", side_effect=boom):
                loop.run_until_complete(
                    asyncio.wait_for(ws_protocol.connect_ws_asyncio(factory), timeout=1)
                )
            self.assertEqual(len(calls), 1)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_irc_data_received_wraps_and_closes_on_error(self):
        from evennia.server.portal import irc

        proto = object.__new__(irc._AsyncioIRCClientProtocol)
        proto.session = MagicMock()
        proto.session.dataReceived.side_effect = ValueError("hostile line")

        # must not propagate to the asyncio loop; must close the connection
        proto.data_received(b"garbage")
        proto.session.transport.loseConnection.assert_called_once()


class TestIrcSslFallback(TestCase):
    """The Twisted IRC SSL fallback must verify the server certificate."""

    def test_twisted_ssl_fallback_uses_verifying_context(self):
        from django.test import override_settings

        from evennia.server.portal import irc

        factory = MagicMock()
        factory.ssl = True
        factory.port = 6697
        factory.network = "irc.example.org"

        with (
            mock.patch(
                "evennia.server.portal.asyncio_transport.asyncio_servers_enabled",
                return_value=False,
            ),
            mock.patch.object(irc, "reactor") as mock_reactor,
            mock.patch("twisted.internet.ssl.optionsForClientTLS") as mock_opts,
            override_settings(PORTAL_ASYNCIO_SERVERS=False),
        ):
            irc.connect_irc(factory)

        mock_opts.assert_called_once_with("irc.example.org")
        mock_reactor.connectSSL.assert_called_once()
