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

    @patch.object(webclient_mod.settings, "WEBSOCKET_MAX_OUTGOING_BYTES", 24, create=True)
    def test_oversized_resume_frame_does_not_advance_state(self):
        t = _Transport()
        with self.assertRaisesRegex(ValueError, "outgoing WebSocket frame"):
            t.sendEncoded({"t": "render", "body": "é" * 20})
        self.assertFalse(hasattr(t, "out_seq"))
        self.assertFalse(hasattr(t, "out_buffer"))
        self.assertEqual(t.sent, [])

    @patch.object(webclient_mod.settings, "WEBSOCKET_MAX_OUTGOING_BYTES", 8, create=True)
    def test_oversized_nonresume_and_binary_frames_are_rejected(self):
        for resume, data, binary in ((False, "é" * 5, False), (True, b"123456789", True)):
            with self.subTest(resume=resume, binary=binary):
                t = _Transport(resume=resume)
                with self.assertRaisesRegex(ValueError, "outgoing WebSocket frame"):
                    t.sendEncoded(data, is_binary=binary)
                self.assertEqual(t.sent, [])

    @patch.object(webclient_mod.settings, "WEBSOCKET_MAX_OUTGOING_BYTES", 32, create=True)
    def test_sendline_counts_serialized_escaping_and_stamp(self):
        t = _Transport()
        with self.assertRaises(ValueError):
            t.sendLine({"t": "x", "body": 'é"\\'})
        self.assertFalse(hasattr(t, "out_seq"))

    def test_exact_outgoing_byte_limit_is_accepted(self):
        payload = {"t": "render", "body": 'é"\\', "s": 1}
        limit = len(json.dumps(payload).encode("utf-8"))
        with patch.object(
            webclient_mod.settings, "WEBSOCKET_MAX_OUTGOING_BYTES", limit, create=True
        ):
            t = _Transport()
            t.sendEncoded({"t": "render", "body": 'é"\\'})
        self.assertEqual(len(t.sent[0]), limit)

    def test_invalid_byte_setting_is_rejected(self):
        names = (
            "WEBSOCKET_MAX_OUTGOING_BYTES",
            "WEBSOCKET_BATCH_BYTES",
            "WEBSOCKET_RESUME_BYTES",
            "WEBSOCKET_RESUME_STASH_BYTES",
        )
        for name in names:
            for value in (0, -1, True, "1024"):
                with (
                    self.subTest(name=name, value=value),
                    patch.object(webclient_mod.settings, name, value, create=True),
                ):
                    with self.assertRaisesRegex(ValueError, name):
                        webclient_mod._setting_bytes(name, 1)


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

    def test_unicode_replay_obeys_live_and_global_byte_budgets(self):
        """Replay accounting measures escaped JSON, including Unicode expansion."""
        content = {"t": "render", "body": "🙂漢"}
        size = len(json.dumps({**content, "s": 1}).encode("utf-8"))
        with (
            patch.object(webclient_mod.settings, "WEBSOCKET_RESUME_BYTES", size, create=True),
            patch.object(webclient_mod.settings, "WEBSOCKET_RESUME_STASH_BYTES", size, create=True),
        ):
            for token in ("first", "second"):
                transport = _Transport(uid=7)
                transport.sendEncoded(content)
                transport.sendEncoded(content)
                self.assertEqual(len(transport.out_buffer), 1)
                self.assertEqual(transport.out_buffer[0][0], 2)
                line = transport.out_buffer[0][1]
                self.assertTrue(line.isascii())
                self.assertEqual(len(line), len(line.encode("utf-8")))
                transport.resume_token = token
                transport._stash_for_resume()
            self.assertEqual(len(webclient_mod._RESUME_STASH), 1)
            self.assertIn("second", webclient_mod._RESUME_STASH)

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

    @patch.object(webclient_mod.settings, "WEBSOCKET_RESUME_BYTES", 90, create=True)
    def test_live_replay_evicts_oldest_whole_frames_by_bytes(self):
        t = _Transport(uid=7)
        for n in range(4):
            t.sendEncoded({"t": "render", "n": n, "body": "x" * 20})
        self.assertLessEqual(sum(len(frame.encode("utf-8")) for _, frame in t.out_buffer), 90)
        self.assertEqual(t.out_buffer[-1][0], t.out_seq)
        self.assertGreater(t.out_buffer[0][0], 1)

    @patch.object(webclient_mod.settings, "WEBSOCKET_RESUME_BYTES", 90, create=True)
    def test_detached_replay_keeps_byte_cap_during_late_output(self):
        old = self._disconnected()
        for n in range(4):
            old.sendEncoded({"t": "render", "n": n, "body": "x" * 20})
        stash = webclient_mod._RESUME_STASH["tok"]
        self.assertLessEqual(sum(len(frame.encode("utf-8")) for _, frame in stash["frames"]), 90)
        self.assertEqual(stash["last_seq"], old.out_seq)

    @patch.object(webclient_mod.settings, "WEBSOCKET_RESUME_STASH_BYTES", 120, create=True)
    def test_global_stash_byte_cap_evicts_oldest_stash(self):
        for token in ("old", "new"):
            t = _Transport(uid=7)
            t.sendEncoded({"t": "render", "body": "x" * 50})
            t.resume_token = token
            t._stash_for_resume()
            webclient_mod._RESUME_STASH[token]["deadline"] += token == "new"
        self.assertNotIn("old", webclient_mod._RESUME_STASH)
        self.assertIn("new", webclient_mod._RESUME_STASH)

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

    def test_direct_send_preserves_pending_order_at_sequence_rollover(self):
        """Direct protocol replies cannot invalidate an admitted batch stamp."""
        transport = _Transport(caps={"batching": True})
        transport.out_seq = 8
        pending = {"t": "render", "body": "x"}
        limit = len(json.dumps({**pending, "s": 9}).encode("utf-8"))
        with patch.object(
            webclient_mod.settings, "WEBSOCKET_MAX_OUTGOING_BYTES", limit, create=True
        ):
            transport.sendEncoded(pending)
            transport.sendLine({"t": "hello"})
            transport._flush_batch()
        self.assertEqual([frame["t"] for frame in _frames(transport)], ["render", "hello"])
        self.assertEqual([frame["s"] for frame in _frames(transport)], [9, 10])

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

    @patch.object(webclient_mod.settings, "WEBSOCKET_BATCH_BYTES", 100, create=True)
    def test_batch_target_splits_at_whole_envelope_boundaries(self):
        t = _Transport(caps={"batching": True})
        for n in range(3):
            t.sendEncoded({"t": "render", "n": n, "body": "é" * 8})
        t._flush_batch()
        envs = _frames(t)
        self.assertEqual([env["s"] for env in envs], list(range(1, len(envs) + 1)))
        members = []
        for env in envs:
            members.extend(env["frames"] if env["t"] == "batch" else [env])
        self.assertEqual([member["n"] for member in members], [0, 1, 2])
        self.assertGreater(len(envs), 1)
        self.assertTrue(all(len(raw) <= 100 for raw in t.sent))

    @patch.object(webclient_mod.settings, "WEBSOCKET_BATCH_BYTES", 100, create=True)
    def test_pending_batch_is_byte_bounded_before_scheduled_flush(self):
        t = _Transport(caps={"batching": True})
        for n in range(3):
            t.sendEncoded({"t": "render", "n": n, "body": "x" * 20})
            pending = getattr(t, "_batch_pending", [])
            if pending:
                wrapper = {
                    "t": "batch",
                    "frames": pending,
                    "s": getattr(t, "out_seq", 0) + 1,
                }
                self.assertLessEqual(len(json.dumps(wrapper).encode("utf-8")), 100)
        self.assertGreaterEqual(len(t.sent), 1)

    @patch.object(webclient_mod.settings, "WEBSOCKET_BATCH_BYTES", 20, create=True)
    def test_singleton_above_batch_target_sends_immediately(self):
        t = _Transport(caps={"batching": True})
        t.sendEncoded({"t": "render", "body": "x" * 30})
        self.assertEqual(len(t.sent), 1)
        self.assertEqual(getattr(t, "_batch_pending", []), [])

    @patch.object(webclient_mod.settings, "WEBSOCKET_BATCH_BYTES", 20, create=True)
    def test_atomic_frame_may_exceed_batch_target(self):
        t = _Transport(caps={"batching": True})
        t.sendEncoded({"t": "render", "body": "x" * 30})
        t._flush_batch()
        self.assertEqual(len(t.sent), 1)
        self.assertEqual(_frames(t)[0]["t"], "render")

    @patch.object(webclient_mod.settings, "WEBSOCKET_MAX_OUTGOING_BYTES", 30, create=True)
    def test_oversized_queued_frame_changes_no_sequence_or_buffer(self):
        t = _Transport(caps={"batching": True})
        with self.assertRaises(ValueError):
            t.sendEncoded({"t": "render", "body": "x" * 40})
        self.assertEqual(getattr(t, "_batch_pending", []), [])
        self.assertFalse(hasattr(t, "out_seq"))

    def test_seq_width_rollover_rejects_incoming_without_flushing_pending(self):
        t = _Transport(caps={"batching": True})
        t.out_seq = 9
        t._batch_pending = [{"t": "render", "body": "kept"}]
        incoming = {"t": "render", "body": "x"}
        stamped = {**incoming, "s": 11}
        limit = len(json.dumps(stamped).encode("utf-8")) - 1
        with (
            patch.object(webclient_mod.settings, "WEBSOCKET_BATCH_BYTES", 20, create=True),
            patch.object(
                webclient_mod.settings, "WEBSOCKET_MAX_OUTGOING_BYTES", limit, create=True
            ),
            self.assertRaises(ValueError),
        ):
            t.sendEncoded(incoming)
        self.assertEqual(t._batch_pending, [{"t": "render", "body": "kept"}])
        self.assertEqual(t.sent, [])
        self.assertEqual(t.out_seq, 9)

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
