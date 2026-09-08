"""
Transport-level behaviour of the webclient protocol: resume sequencing for
wire formats that encode their own frames, the reconnect handshake, and opt-in
frame batching.

These exercise ``WebSocketClient`` methods against a bare instance rather than a
live connection — the logic under test is framing and buffering, not I/O.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.db import connections

from evennia.server.portal import webclient as webclient_mod
from evennia.server.portal.webclient import BATCH_MAX_FRAMES, RESUME_STASH_MAX, WebSocketClient


class _Transport(WebSocketClient):
    """A protocol instance with I/O stubbed out.

    ``WebSocketClient.__init__`` wants a live connection, so build the object
    without running it and set only what these paths touch.
    """

    def __init__(self, *, resume=True, caps=None, uid=None):
        self.sent = []
        self.uid = uid
        self.protocol_flags = {"AZABAN_CAPS": caps} if caps is not None else {}
        self.wire_format = Mock(supports_resume=resume)
        self.sendMessage = Mock(side_effect=lambda data, isBinary=False: self.sent.append(data))

    def disconnect(self, reason=None):  # pragma: no cover - not reached in these tests
        raise AssertionError("disconnect should not be called")


class _Closing(_Transport):
    """A transport that runs the real ``disconnect``.

    Records sends and the close in one ordered log, so a test can assert that
    queued frames reach the wire *before* the close frame.
    """

    disconnect = WebSocketClient.disconnect

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.events = []
        self.nonce = 0
        self.logged_in = True
        self.sessionhandler = Mock(_disconnect_all=False)
        self.sendMessage = Mock(side_effect=self._record_send)
        self.sendClose = Mock(side_effect=lambda *a, **kw: self.events.append("close"))

    def _record_send(self, data, isBinary=False):
        self.sent.append(data)
        self.events.append("send")

    def get_client_session(self):
        return None


def _frames(transport):
    """Decode everything the transport put on the wire."""
    return [json.loads(raw.decode("utf-8")) for raw in transport.sent]


class TestResumeStamping(TestCase):
    """Encoded frames must be sequenced and buffered like ``sendLine`` ones."""

    def test_encoded_frames_are_stamped(self):
        t = _Transport()
        t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))
        t.sendEncoded(json.dumps({"t": "prompt"}).encode("utf-8"))
        self.assertEqual([f["s"] for f in _frames(t)], [1, 2])

    def test_encoded_frames_are_buffered_for_replay(self):
        t = _Transport()
        t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))
        self.assertEqual(len(t.out_buffer), 1)
        self.assertEqual(t.out_seq, 1)

    def test_formats_without_resume_are_left_alone(self):
        t = _Transport(resume=False)
        t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))
        self.assertNotIn("s", _frames(t)[0])
        self.assertFalse(hasattr(t, "out_buffer"))

    def test_binary_frames_are_never_stamped(self):
        t = _Transport()
        t.sendEncoded(b"\x00\x01\x02", is_binary=True)
        self.assertEqual(t.sent, [b"\x00\x01\x02"])

    def test_non_json_payload_passes_through(self):
        # A resume-capable format should always emit JSON, but a malformed frame
        # must still reach the client rather than being swallowed.
        t = _Transport()
        t.sendEncoded(b"not json")
        self.assertEqual(t.sent, [b"not json"])

    def test_envelope_dicts_are_stamped_without_a_round_trip(self):
        # A format that hands over the object skips the parse-stamp-reserialize.
        t = _Transport()
        t.sendEncoded({"t": "render"})
        t.sendEncoded({"t": "prompt"})
        self.assertEqual([f["s"] for f in _frames(t)], [1, 2])

    def test_envelope_dicts_are_serialized_for_formats_without_resume(self):
        t = _Transport(resume=False)
        t.sendEncoded({"t": "render"})
        self.assertEqual(_frames(t)[0], {"t": "render"})


class TestResumeHandshake(TestCase):
    """The reconnect handshake: replay what was missed, then re-base the client."""

    def setUp(self):
        webclient_mod._RESUME_STASH.clear()
        self.addCleanup(webclient_mod._RESUME_STASH.clear)

    def _disconnected(self, *, uid=7, token="tok"):
        """A transport that sent three frames and then stashed them."""
        t = _Transport(uid=uid)
        for i in range(3):
            t.sendEncoded({"t": "render", "n": i})
        t.resume_token = token
        t._stash_for_resume()
        return t

    def test_hello_is_answered_even_without_a_resume_block(self):
        t = _Transport()
        t._handle_client_hello({"t": "hello"})
        env = _frames(t)[-1]
        self.assertEqual(env["t"], "hello")
        self.assertFalse(env["resumed"])

    def test_fresh_hello_restarts_the_sequence(self):
        # The client re-bases off this `s`, so it must reflect the new counter.
        t = _Transport()
        t._handle_client_hello({"t": "hello", "resume": {"token": "tok", "last_seq": 400}})
        self.assertEqual(_frames(t)[-1]["s"], 1)

    def test_reconnect_replays_only_unseen_frames(self):
        self._disconnected()
        t = _Transport(uid=7)
        t._handle_client_hello({"t": "hello", "resume": {"token": "tok", "last_seq": 1}})
        envs = _frames(t)
        self.assertEqual([e["n"] for e in envs if e["t"] == "render"], [1, 2])
        self.assertTrue(envs[-1]["resumed"])

    def test_hello_is_stamped_above_everything_it_replayed(self):
        # The client assigns its cursor from `hello`, so a lower seq here would
        # ask the next reconnect to resend frames it already has.
        self._disconnected()
        t = _Transport(uid=7)
        t._handle_client_hello({"t": "hello", "resume": {"token": "tok", "last_seq": 0}})
        envs = _frames(t)
        replayed = [e["s"] for e in envs if e["t"] == "render"]
        self.assertEqual(envs[-1]["s"], max(replayed) + 1)

    def test_stash_is_not_replayed_to_another_uid(self):
        self._disconnected(uid=7)
        t = _Transport(uid=9)
        t._handle_client_hello({"t": "hello", "resume": {"token": "tok", "last_seq": 0}})
        envs = _frames(t)
        self.assertEqual([e for e in envs if e["t"] == "render"], [])
        self.assertFalse(envs[-1]["resumed"])

    def test_stash_survives_a_rejected_claim_being_consumed(self):
        # A wrong-uid claim pops the stash; that is fine (the owner's own
        # reconnect brings a fresh buffer) but it must not leak frames.
        self._disconnected(uid=7)
        _Transport(uid=9)._handle_client_hello({"t": "hello", "resume": {"token": "tok"}})
        self.assertNotIn("tok", webclient_mod._RESUME_STASH)

    def test_stash_is_capped(self):
        for n in range(RESUME_STASH_MAX + 10):
            t = _Transport(uid=1)
            t.sendEncoded({"t": "render"})
            t.resume_token = f"tok{n}"
            t._stash_for_resume()
        self.assertLessEqual(len(webclient_mod._RESUME_STASH), RESUME_STASH_MAX)

    def test_reconnect_replays_output_after_socket_loss(self):
        """A retained Portal session keeps recording during its grace window."""
        old = self._disconnected()
        old.sendMessage = Mock()
        old.sendEncoded({"t": "render", "n": 3})
        fresh = _Transport(uid=7)
        fresh._handle_client_hello({"resume": {"token": "tok", "last_seq": 3}})
        self.assertEqual([e["n"] for e in _frames(fresh) if e["t"] == "render"], [3])
        self.assertEqual(_frames(fresh)[-1]["s"], 5)
        old.sendEncoded({"t": "render", "n": 4})
        self.assertNotIn("tok", webclient_mod._RESUME_STASH)
        self.assertEqual(fresh.out_seq, 5)

    def test_retained_output_keeps_capacity_and_original_deadline(self):
        """New output cannot extend retention or grow the bounded replay buffer."""
        old = self._disconnected()
        deadline = webclient_mod._RESUME_STASH["tok"]["deadline"]
        for n in range(webclient_mod.RESUME_BUFFER_MAX + 5):
            old.sendEncoded({"t": "render", "n": n})
        stash = webclient_mod._RESUME_STASH["tok"]
        self.assertEqual(stash["deadline"], deadline)
        self.assertEqual(len(stash["frames"]), webclient_mod.RESUME_BUFFER_MAX)
        self.assertEqual(stash["last_seq"], old.out_seq)

    def test_old_socket_cannot_update_a_replacement_stash(self):
        """Reuse of a token does not join two sockets' replay streams."""
        old = self._disconnected()
        replacement = self._disconnected()
        old.sendEncoded({"t": "render", "n": "stale"})
        stash = webclient_mod._RESUME_STASH["tok"]
        self.assertEqual(stash["last_seq"], replacement.out_seq)
        self.assertNotIn("stale", str(stash["frames"]))

    def test_retained_output_cannot_revive_an_expired_stash(self):
        """The reconnect grace period ends even if the Server keeps sending."""
        old = self._disconnected()
        deadline = webclient_mod._RESUME_STASH["tok"]["deadline"]
        with patch.object(webclient_mod.time, "time", return_value=deadline + 1):
            old.sendEncoded({"t": "render", "n": "too late"})
            fresh = _Transport(uid=7)
            fresh._handle_client_hello({"resume": {"token": "tok", "last_seq": 0}})
        self.assertFalse(_frames(fresh)[-1]["resumed"])
        self.assertEqual([e for e in _frames(fresh) if e["t"] == "render"], [])


class TestRecoveryBrowserAuth(TestCase):
    """Authoritative recovery repairs browser auth without replaying login."""

    def test_recovery_persists_missing_login_stamp_once(self):
        """A lost login notification must not prevent the next auto-login."""
        from evennia.server.portal.portalsessionhandler import PortalSessionHandler

        browser = {"webclient_authenticated_nonce": 0}
        store = Mock(wraps=browser)
        store.save = Mock()
        store.get = browser.get
        store.__setitem__ = Mock(side_effect=browser.__setitem__)
        client = _Transport(uid=None)
        handler = PortalSessionHandler()
        client.init_session("websocket", "127.0.0.1", handler)
        client.sessid = 1
        handler[1] = client
        handler._ensure_bus_socket(client)
        client.get_client_session = Mock(return_value=store)
        client.at_login = Mock()
        payload = {
            "sessions": {1: {"_socket_id": client._bus_socket_id, "uid": 42, "logged_in": True}},
            "closed": {},
        }
        handler.apply_bus_state(payload)
        self.assertEqual(browser.get("webclient_authenticated_uid"), 42)
        handler.apply_bus_state(payload)
        store.save.assert_called_once()
        client.at_login.assert_not_called()

    def test_recovery_preserves_a_newer_browser_login(self):
        """A retained socket cannot replace another login's browser stamp."""
        store = Mock()
        store.get.side_effect = {
            "webclient_authenticated_nonce": 8,
            "webclient_authenticated_uid": 99,
        }.get
        store.__setitem__ = Mock()
        client = _Transport(uid=42)
        client.nonce = 7
        client.logged_in = True
        client.get_client_session = Mock(return_value=store)
        client.at_auth_sync()
        store.__setitem__.assert_not_called()
        store.save.assert_not_called()

    def test_recovered_logout_clears_its_own_browser_stamp(self):
        """An authoritative logout does not leave a reusable login stamp."""
        store = Mock()
        store.get.side_effect = {
            "webclient_authenticated_nonce": 7,
            "webclient_authenticated_uid": 42,
        }.get
        store.__setitem__ = Mock()
        client = _Transport(uid=None)
        client.nonce = 7
        client.logged_in = False
        client.get_client_session = Mock(return_value=store)
        client.at_auth_sync()
        store.__setitem__.assert_called_once_with("webclient_authenticated_uid", None)
        store.save.assert_called_once()


class TestNativeCallbackDatabaseScope(TestCase):
    """Native protocol callbacks own and close their database wrappers."""

    def test_callbacks_detach_inherited_wrapper_and_close_on_failure(self):
        """Both receive and disconnect release their scope even on errors."""
        from evennia.server.portal.webclient import AsyncioWebSocketProtocol

        async def exercise():
            """Run under asyncio's ContextVar-backed Django storage."""
            parent = connections["default"]
            observed = []
            closed = []

            def callback(*args):
                """Open a wrapper and simulate a failing protocol hook."""
                observed.append(connections["default"])
                raise ValueError("protocol failure")

            protocol = AsyncioWebSocketProtocol(None)
            protocol.ws = SimpleNamespace(dataReceived=callback, connectionLost=callback)
            with patch.object(
                connections, "close_all", side_effect=lambda: closed.append(connections["default"])
            ):
                for method, arg in (
                    (protocol.data_received, b"data"),
                    (protocol.connection_lost, None),
                ):
                    with self.assertRaisesRegex(ValueError, "protocol failure"):
                        method(arg)
            self.assertEqual(closed, observed)
            self.assertEqual(len(observed), 2)
            self.assertIsNot(observed[0], observed[1])
            self.assertTrue(all(wrapper is not parent for wrapper in observed))
            self.assertIs(connections["default"], parent)

        asyncio.run(exercise())


class TestBatching(TestCase):
    """Bursts coalesce only for clients that asked for it."""

    def setUp(self):
        self.loop = Mock()
        self.running_loop = patch.object(
            webclient_mod.asyncio, "get_running_loop", return_value=self.loop
        )
        self.running_loop.start()
        self.addCleanup(self.running_loop.stop)

    def test_batching_is_off_without_the_cap(self):
        t = _Transport(caps={})
        t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))
        self.assertEqual(len(t.sent), 1)
        self.assertEqual(_frames(t)[0]["t"], "render")

    def test_queued_frames_are_not_sent_until_flush(self):
        t = _Transport(caps={"batching": True})
        t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))
        t.sendEncoded(json.dumps({"t": "prompt"}).encode("utf-8"))
        self.assertEqual(t.sent, [])
        self.loop.call_soon.assert_called_once_with(t._flush_batch)

    def test_without_running_loop_flushes_immediately(self):
        with patch.object(webclient_mod.asyncio, "get_running_loop", side_effect=RuntimeError):
            t = _Transport(caps={"batching": True})
            t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))

        self.assertEqual(_frames(t)[0]["t"], "render")

    def test_flush_coalesces_in_order(self):
        t = _Transport(caps={"batching": True})
        t.sendEncoded({"t": "render", "n": 1})
        t.sendEncoded(json.dumps({"t": "render", "n": 2}).encode("utf-8"))
        t._flush_batch()
        env = _frames(t)[0]
        self.assertEqual(env["t"], "batch")
        self.assertEqual([f["n"] for f in env["frames"]], [1, 2])

    def test_batch_carries_one_seq(self):
        t = _Transport(caps={"batching": True})
        t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))
        t.sendEncoded(json.dumps({"t": "prompt"}).encode("utf-8"))
        t._flush_batch()
        self.assertEqual(_frames(t)[0]["s"], 1)
        self.assertEqual(t.out_seq, 1)

    def test_single_frame_is_not_wrapped(self):
        t = _Transport(caps={"batching": True})
        t.sendEncoded(json.dumps({"t": "render"}).encode("utf-8"))
        t._flush_batch()
        self.assertEqual(_frames(t)[0]["t"], "render")

    def test_flush_with_nothing_queued_is_a_noop(self):
        t = _Transport(caps={"batching": True})
        t._flush_batch()
        self.assertEqual(t.sent, [])

    def test_full_buffer_flushes_without_waiting(self):
        t = _Transport(caps={"batching": True})
        for i in range(BATCH_MAX_FRAMES):
            t.sendEncoded(json.dumps({"t": "render", "n": i}).encode("utf-8"))
        self.assertEqual(len(t.sent), 1)
        self.assertEqual(len(_frames(t)[0]["frames"]), BATCH_MAX_FRAMES)

    def test_disconnect_drains_the_queue_before_closing(self):
        # The `logout` OOB behind a server-side quit is queued in the same loop
        # iteration as the disconnect; without the drain it would be written
        # after the close frame and lost, and the shell would just reconnect.
        t = _Closing(caps={"batching": True})
        t.sendEncoded(json.dumps({"t": "oob", "event": "logout"}).encode("utf-8"))
        t.disconnect()
        self.assertEqual(t.events, ["send", "close"])
        self.assertEqual(_frames(t)[0]["event"], "logout")

    def test_disconnect_is_not_reentered(self):
        t = _Closing(caps={"batching": True})
        t.disconnect()
        t.disconnect()
        self.assertEqual(t.sendClose.call_count, 1)

    def test_malformed_member_falls_back_to_individual_sends(self):
        # One bad frame must not swallow the whole burst.
        t = _Transport(caps={"batching": True})
        t._batch_pending = [json.dumps({"t": "render"}), "not json"]
        t._flush_batch()
        self.assertEqual(len(t.sent), 2)
        self.assertEqual(json.loads(t.sent[0].decode("utf-8"))["t"], "render")
        self.assertEqual(t.sent[1], b"not json")
