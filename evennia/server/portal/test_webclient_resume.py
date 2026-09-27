"""
Session continuity for the web client: a dropped socket does not end the game
session.

These drive real ``WebSocketClient`` sessions through a real
``PortalSessionHandler``, with the Server link and the socket I/O stubbed, and
check what the Server is told. The property under test is that a phone losing
its connection for a few seconds costs the player nothing: no logout, no login,
no lost output, and no browser sign-out.
"""

import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import evennia
from evennia.server.portal import amp
from evennia.server.portal import portalsessionhandler as psh_mod
from evennia.server.portal import webclient as webclient_mod
from evennia.server.portal.portalsessionhandler import PortalSessionHandler
from evennia.server.portal.webclient import CLOSE_SUPERSEDED, WebSocketClient
from evennia.server.portal.wire_formats import WIRE_FORMATS
from evennia.server.portal.ws_protocol import Disconnected


class _Wire:
    """The transport surface a portal socket touches."""

    def __init__(self, host):
        self.host = host
        self.closed = False
        self.aborted = False

    def write(self, data):
        pass

    def loseConnection(self):
        self.closed = True

    def abortConnection(self):
        self.aborted = True

    def getPeer(self):
        return SimpleNamespace(host=self.host, port=50000)

    def setTcpKeepAlive(self, enabled):
        pass


class _Browser(dict):
    """The Django session a handshake presents."""

    saves = 0

    def save(self):
        self.saves += 1


def _signed_in(uid=7, nonce=3):
    return _Browser(webclient_authenticated_uid=uid, webclient_authenticated_nonce=nonce)


class _ContinuityBase(TestCase):
    """A portal with one session handler and a stubbed Server link."""

    def setUp(self):
        webclient_mod._RESUMABLE.clear()
        webclient_mod._LIVE_SOCKETS.clear()
        psh_mod._CONNECTION_QUEUE.clear()
        self.addCleanup(webclient_mod._RESUMABLE.clear)
        self.addCleanup(psh_mod._CONNECTION_QUEUE.clear)

        self.link = MagicMock()
        self.link.ready = True
        self.link.send_AdminPortal2Server.return_value = Mock(admitted=True)
        self.link.send_MsgPortal2Server.return_value = Mock(admitted=True)
        for name, value in (
            ("EVENNIA_PORTAL_SERVICE", SimpleNamespace(server_amp=self.link)),
            ("PORTAL_SESSION_HANDLER", None),
        ):
            previous = getattr(evennia, name, None)
            self.addCleanup(setattr, evennia, name, previous)
            setattr(evennia, name, value)

        self.handler = PortalSessionHandler()
        evennia.PORTAL_SESSION_HANDLER = self.handler
        # Let every connect reach the Server at once instead of being throttled.
        self.handler.connection_last = 0

        for target in (
            patch.object(psh_mod, "clock", MagicMock()),
            patch("evennia.moderation.portal_guard.refuses", return_value=False),
            patch("evennia.moderation.ratelimit.rate_limited", return_value=False),
            patch.object(webclient_mod.logger, "log_info"),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.warn = patch.object(webclient_mod.logger, "log_warn").start()
        self.addCleanup(patch.stopall)

    # -- helpers ----------------------------------------------------------

    def _socket(self, browser, host="203.0.113.7", fmt="azaban.v1"):
        """A socket past its WebSocket handshake, before ``onOpen``."""
        sock = WebSocketClient()
        sock.factory = SimpleNamespace(sessionhandler=self.handler)
        sock.transport = _Wire(host)
        sock.http_headers = {}
        sock.http_request_uri = "/?csess&&ua"
        sock.wire_format = WIRE_FORMATS[fmt]
        sock._ws_open = True
        sock.frames = []
        sock.sendMessage = Mock(
            side_effect=lambda data, isBinary=False: sock.frames.append(json.loads(data))
        )
        sock.sendClose = Mock()
        sock.sendPing = Mock(return_value=True)

        def client_session():
            sock.csessid = "csess"
            return browser

        sock.get_client_session = client_session
        return sock

    def _hello(self, sock, token=None, last_seq=0):
        sock.onMessage(
            json.dumps(
                {"t": "hello", "caps": {}, "resume": {"token": token, "last_seq": last_seq}}
            ).encode(),
            False,
        )
        return sock.frames[-1]

    def _connected(self, browser=None, host="203.0.113.7"):
        """An open, signed-in shell socket that has had its hello answered."""
        sock = self._socket(browser if browser is not None else _signed_in(), host=host)
        sock.onOpen()
        self._hello(sock)
        return sock

    def _say(self, sock, text):
        """Server output for ``sock``'s session, routed as the Portal routes it."""
        self.handler.data_out(self.handler.get(sock.sessid), text=[[text], {}])

    def _ops(self):
        return [c.kwargs.get("operation") for c in self.link.send_AdminPortal2Server.mock_calls]

    def _texts(self, sock):
        """Lines of Server text the socket put on the wire (Azaban sends them as nodes)."""
        return [
            node.get("body", "")
            for frame in sock.frames
            if frame.get("t") == "render"
            for node in frame.get("nodes", [])
        ]


class TestHold(_ContinuityBase):
    """What a lost socket does to its session."""

    def test_unclean_drop_holds_the_session_without_telling_the_server(self):
        sock = self._connected()
        ops = len(self._ops())
        sock.connectionLost("gone")
        self.assertIsNotNone(sock._held_until)
        self.assertIs(self.handler.get(sock.sessid), sock)
        self.assertNotIn(amp.PDISCONN, self._ops()[ops:])

    def test_output_while_held_is_recorded_not_written(self):
        sock = self._connected()
        sock.connectionLost("gone")
        sent = sock.sendMessage.call_count
        self._say(sock, "the rain keeps on")
        self.assertEqual(sock.sendMessage.call_count, sent)
        self.assertIn("the rain keeps on", sock.out_buffer[-1][1])

    def test_output_while_held_neither_extends_the_hold_nor_outgrows_the_buffer(self):
        sock = self._connected()
        sock.connectionLost("gone")
        deadline = sock._held_until
        for n in range(webclient_mod.RESUME_BUFFER_MAX + 5):
            self._say(sock, "line %d" % n)
        self.assertEqual(sock._held_until, deadline)
        self.assertEqual(len(sock.out_buffer), webclient_mod.RESUME_BUFFER_MAX)

    def test_a_close_frame_with_another_code_holds_and_keeps_the_browser_signed_in(self):
        # The production failure: a peer close that was neither 1000 nor 1001
        # left a half-closed socket, the next line of room chatter raised
        # Disconnected, and that ran a full logout that also cleared the
        # browser's auto-login stamp - so the reconnect came back signed out.
        browser = _signed_in()
        sock = self._connected(browser)
        sock._notify_close(True, 1005, "")
        self._say(sock, "someone arrives")
        self.assertIsNotNone(sock._held_until)
        self.assertEqual(browser["webclient_authenticated_uid"], 7)
        self.assertNotIn(amp.PDISCONN, self._ops())

    def test_a_write_that_finds_the_socket_dead_holds(self):
        browser = _signed_in()
        sock = self._connected(browser)
        sock.sendMessage.side_effect = Disconnected()
        self._say(sock, "into the void")
        self.assertIsNotNone(sock._held_until)
        self.assertEqual(browser["webclient_authenticated_uid"], 7)
        self.assertIn("into the void", sock.out_buffer[-1][1])

    def test_tab_close_ends_the_session_and_signs_the_browser_out(self):
        browser = _signed_in()
        sock = self._connected(browser)
        sock.onClose(True, 1001, "")
        self.assertIsNone(sock._held_until)
        self.assertIn(amp.PDISCONN, self._ops())
        self.assertIsNone(browser["webclient_authenticated_uid"])

    def test_an_anonymous_session_is_not_held(self):
        sock = self._connected(_Browser())
        sock.connectionLost("gone")
        self.assertIsNone(sock._held_until)
        self.assertIn(amp.PDISCONN, self._ops())

    def test_a_client_without_resume_is_not_held(self):
        sock = self._socket(_signed_in(), fmt="v1.evennia.com")
        sock.onOpen()
        sock.connectionLost("gone")
        self.assertIsNone(sock._held_until)
        self.assertIn(amp.PDISCONN, self._ops())

    def test_a_drop_before_hello_never_reaches_the_server(self):
        sock = self._socket(_signed_in())
        sock.onOpen()
        sock.connectionLost("gone")
        self.assertEqual(self._ops(), [])

    @patch.object(webclient_mod.settings, "WEBCLIENT_RESUME_GRACE", 0, create=True)
    def test_a_zero_grace_turns_holding_off(self):
        sock = self._connected()
        sock.connectionLost("gone")
        self.assertIn(amp.PDISCONN, self._ops())

    def test_an_unclaimed_hold_ends_without_signing_the_browser_out(self):
        browser = _signed_in()
        sock = self._connected(browser)
        token = sock.resume_token
        sock.connectionLost("gone")
        sock._expire_hold()
        self.assertIn(amp.PDISCONN, self._ops())
        self.assertIsNone(self.handler.get(sock.sessid))
        self.assertNotIn(token, webclient_mod._RESUMABLE)
        # The browser never asked to leave, so its next visit is still signed in.
        self.assertEqual(browser["webclient_authenticated_uid"], 7)

    def test_the_server_can_still_end_a_held_session(self):
        browser = _signed_in()
        sock = self._connected(browser)
        token = sock.resume_token
        sock.connectionLost("gone")
        self.handler.server_disconnect(sock, reason="Booted.")
        self.assertTrue(sock._ended)
        self.assertNotIn(token, webclient_mod._RESUMABLE)
        # A kick signs the browser out, as it does a live session.
        self.assertIsNone(browser["webclient_authenticated_uid"])

    @patch.object(webclient_mod, "RESUME_HOLD_MAX", 2)
    def test_holds_are_capped_oldest_first(self):
        socks = [self._connected(_signed_in(uid=n)) for n in (1, 2, 3)]
        for sock in socks:
            sock.connectionLost("gone")
        self.assertTrue(socks[0]._ended)
        self.assertFalse(socks[1]._ended or socks[2]._ended)

    def test_overdue_holds_end_even_if_their_timer_never_fired(self):
        sock = self._connected()
        sock.connectionLost("gone")
        sock._held_until = 0.0
        webclient_mod._enforce_hold_limits()
        self.assertTrue(sock._ended)

    def test_the_held_replay_windows_share_a_byte_cap(self):
        first = self._connected(_signed_in(uid=1))
        second = self._connected(_signed_in(uid=2))
        for sock in (first, second):
            self._say(sock, "x" * 400)
            sock.connectionLost("gone")
        first._held_until, second._held_until = 1e12, 1e12 + 1
        limit = webclient_mod._buffer_bytes(second) + 10
        with patch.object(
            webclient_mod.settings, "WEBSOCKET_RESUME_STASH_BYTES", limit, create=True
        ):
            webclient_mod._enforce_hold_limits()
        self.assertTrue(first._ended)
        self.assertFalse(second._ended)


class TestTakeover(_ContinuityBase):
    """A reconnect that proves ownership takes the session over in place."""

    def _drop_and_return(self, browser=None, last_seq=None, live=False):
        browser = browser if browser is not None else _signed_in()
        old = self._connected(browser)
        self._say(old, "before the drop")
        seq_seen = old.out_seq
        if not live:
            old.connectionLost("gone")
            self._say(old, "while you were away")
        ops = len(self._ops())
        new = self._socket(browser, host="198.51.100.9")
        new.onOpen()
        reply = self._hello(
            new, token=old.resume_token, last_seq=seq_seen if last_seq is None else last_seq
        )
        return old, new, reply, self._ops()[ops:]

    def test_the_server_sees_neither_a_logout_nor_a_login(self):
        old, new, reply, ops = self._drop_and_return()
        self.assertTrue(reply["resumed"])
        self.assertNotIn(amp.PDISCONN, ops)
        self.assertNotIn(amp.PCONN, ops)
        self.assertEqual(new.sessid, old.sessid)
        self.assertIs(self.handler.get(new.sessid), new)

    def test_the_bus_keeps_the_same_socket_incarnation(self):
        old, new, _reply, _ops = self._drop_and_return()
        self.assertEqual(new._bus_socket_id, old._bus_socket_id)

    def test_the_new_address_is_synced_to_the_server(self):
        _old, _new, _reply, _ops = self._drop_and_return()
        syncs = [
            c.kwargs["sessiondata"]
            for c in self.link.send_AdminPortal2Server.mock_calls
            if c.kwargs.get("operation") == amp.PCONNSYNC
        ]
        self.assertIn("198.51.100.9", [s.get("address") for s in syncs])

    def test_missed_output_is_replayed_before_the_hello(self):
        _old, new, reply, _ops = self._drop_and_return()
        self.assertEqual(self._texts(new), ["while you were away"])
        replayed = [f["s"] for f in new.frames if f.get("t") == "render"]
        self.assertGreater(reply["s"], max(replayed))

    def test_a_reloaded_page_gets_the_whole_window(self):
        _old, new, reply, _ops = self._drop_and_return(last_seq=0)
        self.assertEqual(self._texts(new), ["before the drop", "while you were away"])
        self.assertNotIn("gap", reply)

    def test_output_now_reaches_the_new_socket(self):
        _old, new, _reply, _ops = self._drop_and_return()
        self._say(new, "welcome back")
        self.assertEqual(self._texts(new)[-1], "welcome back")

    def test_the_token_is_replaced_and_the_old_one_is_spent(self):
        old, new, reply, _ops = self._drop_and_return()
        self.assertNotEqual(reply["token"], old.resume_token)
        self.assertIs(webclient_mod._RESUMABLE[reply["token"]], new)
        self.assertNotIn(old.resume_token, webclient_mod._RESUMABLE)
        # A copied tab presenting the spent token starts a session of its own.
        copy = self._socket(_signed_in(), host="192.0.2.1")
        copy.onOpen()
        self.assertFalse(self._hello(copy, token=old.resume_token)["resumed"])
        self.assertNotEqual(copy.sessid, new.sessid)

    def test_the_session_keeps_its_own_flags_and_takes_the_new_socket_facts(self):
        browser = _signed_in()
        old = self._connected(browser)
        old.protocol_flags["SCREENREADER"] = True
        old.connectionLost("gone")
        new = self._socket(browser, host="198.51.100.9")
        new.onOpen()
        self._hello(new, token=old.resume_token)
        self.assertTrue(new.protocol_flags["SCREENREADER"])
        self.assertEqual(new.address, "198.51.100.9")

    def test_another_account_cannot_claim_the_session(self):
        browser = _signed_in(uid=7)
        old = self._connected(browser)
        self._say(old, "private")
        old.connectionLost("gone")
        thief = self._socket(_signed_in(uid=9), host="192.0.2.66")
        thief.onOpen()
        reply = self._hello(thief, token=old.resume_token)
        self.assertFalse(reply["resumed"])
        self.assertNotIn("private", json.dumps(thief.frames))
        self.assertNotEqual(thief.sessid, old.sessid)
        # The owner can still come back for it.
        self.assertIsNotNone(old._held_until)
        self.warn.assert_called_once()

    def test_a_signed_out_browser_cannot_claim_the_session(self):
        old = self._connected(_signed_in())
        old.connectionLost("gone")
        anon = self._socket(_Browser(), host="192.0.2.66")
        anon.onOpen()
        self.assertFalse(self._hello(anon, token=old.resume_token)["resumed"])
        self.assertIsNotNone(old._held_until)

    def test_a_socket_that_looked_open_is_superseded(self):
        # A phone changed networks before the keepalive noticed: the old socket
        # still looks open when the reconnect arrives.
        old, new, reply, ops = self._drop_and_return(live=True)
        self.assertTrue(reply["resumed"])
        self.assertNotIn(amp.PDISCONN, ops)
        old.sendClose.assert_any_call(CLOSE_SUPERSEDED, "session resumed on another connection")
        self.assertTrue(old.transport.aborted)

    def test_a_superseded_socket_cannot_touch_its_successor(self):
        old, new, _reply, _ops = self._drop_and_return(live=True)
        ops = len(self._ops())
        inputs = self.link.send_MsgPortal2Server.call_count
        old.connectionLost("late close")
        old.disconnect("late disconnect")
        old.onMessage(json.dumps({"t": "cmd", "line": "look"}).encode(), False)
        self.assertEqual(self._ops()[ops:], [])
        self.assertIs(self.handler.get(new.sessid), new)
        self.assertEqual(self.link.send_MsgPortal2Server.call_count, inputs)

    def test_a_socket_that_started_its_own_session_does_not_adopt_another(self):
        browser = _signed_in()
        old = self._connected(browser)
        old.connectionLost("gone")
        late = self._socket(browser, host="198.51.100.9")
        late.onOpen()
        late._connect_session()  # its hello was slower than HELLO_WAIT_SECONDS
        reply = self._hello(late, token=old.resume_token)
        self.assertFalse(reply["resumed"])
        self.assertNotEqual(late.sessid, old.sessid)
        self.assertIsNotNone(old._held_until)

    def test_a_gap_is_reported_when_the_window_moved_past_the_client(self):
        browser = _signed_in()
        old = self._connected(browser)
        self._say(old, "seen")
        seen = old.out_seq
        old.connectionLost("gone")
        with patch.object(webclient_mod, "RESUME_BUFFER_MAX", 2):
            for n in range(4):
                self._say(old, "missed %d" % n)
        new = self._socket(browser, host="198.51.100.9")
        new.onOpen()
        reply = self._hello(new, token=old.resume_token, last_seq=seen)
        self.assertTrue(reply["resumed"])
        self.assertTrue(reply["gap"])
        self.assertEqual(self._texts(new), ["missed 2", "missed 3"])

    def test_a_cleared_log_is_not_replayed(self):
        browser = _signed_in()
        old = self._connected(browser)
        self._say(old, "wiped")
        old.onMessage(json.dumps({"t": "resume_reset"}).encode(), False)
        old.connectionLost("gone")
        new = self._socket(browser, host="198.51.100.9")
        new.onOpen()
        self._hello(new, token=old.resume_token, last_seq=0)
        self.assertEqual(self._texts(new), [])

    def test_an_expired_hold_cannot_be_claimed(self):
        browser = _signed_in()
        old = self._connected(browser)
        old.connectionLost("gone")
        old._expire_hold()
        new = self._socket(browser, host="198.51.100.9")
        new.onOpen()
        self.assertFalse(self._hello(new, token=old.resume_token)["resumed"])


class TestHandshake(_ContinuityBase):
    """When a shell socket reaches the Server, and what never does."""

    def test_a_shell_socket_waits_for_hello_before_connecting(self):
        sock = self._socket(_signed_in())
        sock.onOpen()
        self.assertEqual(self._ops(), [])
        self._hello(sock)
        self.assertIn(amp.PCONN, self._ops())

    def test_other_clients_connect_on_open(self):
        sock = self._socket(_signed_in(), fmt="v1.evennia.com")
        sock.onOpen()
        self.assertIn(amp.PCONN, self._ops())

    def test_any_other_first_frame_starts_the_session(self):
        sock = self._socket(_signed_in())
        sock.onOpen()
        sock.onMessage(json.dumps({"t": "cmd", "line": "look"}).encode(), False)
        self.assertIn(amp.PCONN, self._ops())

    def test_a_heartbeat_is_answered_at_the_portal(self):
        sock = self._socket(_signed_in())
        sock.onOpen()
        sock.onMessage(json.dumps({"t": "ping", "n": 3}).encode(), False)
        self.assertEqual(sock.frames, [{"t": "pong", "n": 3}])
        # It neither starts a session nor reaches one.
        self.assertEqual(self._ops(), [])
        self.link.send_MsgPortal2Server.assert_not_called()

    def test_a_heartbeat_is_never_replayed(self):
        sock = self._connected()
        buffered = len(sock.out_buffer)
        seq = sock.out_seq
        sock.onMessage(json.dumps({"t": "ping", "n": 1}).encode(), False)
        self.assertEqual((len(sock.out_buffer), sock.out_seq), (buffered, seq))
        self.assertNotIn("s", sock.frames[-1])


class TestKeepalive(_ContinuityBase):
    """The portal pings every socket and drops the ones that went silent."""

    def test_a_socket_that_answers_is_pinged(self):
        sock = self._connected()
        sock._keepalive_tick(sock.last_received + 30, timeout=60)
        sock.sendPing.assert_called_once()
        self.assertFalse(sock._link_lost)

    def test_a_silent_socket_is_aborted_and_its_session_held(self):
        sock = self._connected()
        sock._keepalive_tick(sock.last_received + 61, timeout=60)
        self.assertTrue(sock.transport.aborted)
        self.assertIsNotNone(sock._held_until)
        self.assertNotIn(amp.PDISCONN, self._ops())

    def test_the_sweep_skips_sockets_that_are_already_gone(self):
        sock = self._connected()
        sock.connectionLost("gone")
        webclient_mod._LIVE_SOCKETS.add(sock)
        webclient_mod._keepalive_sweep()
        sock.sendPing.assert_not_called()
        self.assertNotIn(sock, webclient_mod._LIVE_SOCKETS)

    @patch.object(webclient_mod.settings, "WEBCLIENT_PING_DELAY", 30, create=True)
    @patch.object(webclient_mod.settings, "WEBCLIENT_PING_TIMEOUT", 10, create=True)
    def test_the_silence_timeout_is_never_below_two_pings(self):
        self.assertEqual(webclient_mod._keepalive_timing(), (30, 60))

    @patch.object(webclient_mod.settings, "WEBCLIENT_PING_DELAY", -1, create=True)
    def test_a_negative_delay_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "WEBCLIENT_PING_DELAY"):
            webclient_mod._keepalive_timing()


class TestRebind(TestCase):
    """The handler swaps a session's socket without the Server noticing."""

    def setUp(self):
        psh_mod._CONNECTION_QUEUE.clear()
        self.addCleanup(psh_mod._CONNECTION_QUEUE.clear)
        self.handler = PortalSessionHandler()
        self.handler.sync = Mock()

    def test_the_entry_is_swapped_and_synced(self):
        old, new = SimpleNamespace(sessid=4), SimpleNamespace(sessid=4)
        self.handler[4] = old
        self.handler.rebind(old, new)
        self.assertIs(self.handler[4], new)
        self.handler.sync.assert_called_once_with(new)

    def test_a_queued_connect_goes_out_for_the_replacement(self):
        old, new = SimpleNamespace(sessid=4), SimpleNamespace(sessid=4)
        self.handler[4] = old
        psh_mod._CONNECTION_QUEUE.appendleft(old)
        self.handler.rebind(old, new)
        self.assertEqual(list(psh_mod._CONNECTION_QUEUE), [new])

    def test_a_replaced_session_cannot_disconnect_its_successor(self):
        old, new = SimpleNamespace(sessid=4), SimpleNamespace(sessid=4)
        self.handler[4] = new
        with patch.object(psh_mod.evennia, "EVENNIA_PORTAL_SERVICE") as service:
            self.handler.disconnect(old)
            self.handler.server_disconnect(SimpleNamespace(sessid=4, disconnect=Mock()))
        service.server_amp.send_AdminPortal2Server.assert_not_called()
        self.assertIs(self.handler[4], new)

    def test_owns_tells_the_live_holder_from_a_leftover(self):
        old, new = SimpleNamespace(sessid=4), SimpleNamespace(sessid=4)
        self.handler[4] = new
        self.assertTrue(self.handler.owns(new))
        self.assertFalse(self.handler.owns(old))
