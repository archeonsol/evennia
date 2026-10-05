"""
Tests for secure AMP session serialization.
"""

import pickle
from unittest import mock

from django.test import SimpleTestCase

from evennia.server import amp_serde
from evennia.server.amp_serde import (
    pack_admin_message,
    pack_launcher_args,
    pack_multicast_message,
    pack_session_message,
    pack_status,
    sanitize_session_kwargs,
    unpack_admin_message,
    unpack_launcher_args,
    unpack_multicast_message,
    unpack_session_message,
    unpack_status,
)
from evennia.utils.test_resources import BaseEvenniaTest


class TestAMPSerde(BaseEvenniaTest):
    def test_roundtrip_json(self):
        kwargs = {"text": "hello", "options": {"raw": True}}
        wire = pack_session_message(7, kwargs)
        self.assertTrue(wire.startswith(b"J1"))
        sessid, out = unpack_session_message(wire)
        self.assertEqual(sessid, 7)
        self.assertEqual(out["text"], "hello")

    def test_rejects_pickle(self):
        blob = pickle.dumps((1, {"text": "x"}), pickle.HIGHEST_PROTOCOL)
        with self.assertRaises(ValueError):
            unpack_session_message(blob)

    def test_rejects_unsafe_types(self):
        with self.assertRaises(TypeError):
            sanitize_session_kwargs({"obj": self.char1})

    def test_large_server_output_roundtrips_untrusted_caps_off(self):
        # Server-generated output (MsgServer2Portal) is trusted and may exceed
        # the per-string cap. It must pack and unpack (Portal-receive direction,
        # enforce_limits=False) without loss.
        big = "x" * (65536 * 2)
        wire = pack_session_message(1, {"text": big})
        sessid, out = unpack_session_message(wire, enforce_limits=False)
        self.assertEqual(sessid, 1)
        self.assertEqual(out["text"], big)

    def test_oversized_player_input_rejected_at_server(self):
        # The same oversized payload arriving as untrusted player input
        # (MsgPortal2Server, the default enforce_limits=True) is rejected,
        # bounding player-driven payloads at the Server.
        wire = pack_session_message(1, {"text": "x" * (65536 * 2)})
        with self.assertRaises(ValueError):
            unpack_session_message(wire)

    def test_multicast_roundtrip_json(self):
        wire = pack_multicast_message([2, 7], {"text": [["same"], {}]})
        self.assertTrue(wire.startswith(b"M1"))
        sessids, out = unpack_multicast_message(wire)
        self.assertEqual(sessids, [2, 7])
        self.assertEqual(out, {"text": [["same"], {}]})

    def test_multicast_rejects_duplicate_or_invalid_sessions(self):
        for sessids in ([], [0], [1, 1]):
            with self.subTest(sessids=sessids), self.assertRaises(ValueError):
                pack_multicast_message(sessids, {"text": "x"})

    def test_status_roundtrip_json(self):
        # The MsgStatus payload is the get_status() 6-tuple. It packs to a JSON
        # ``S1`` envelope and restores as a list (tuples are not preserved by
        # JSON, but the consumer unpacks positionally).
        status = (True, False, 1234, None, {"servername": "test", "telnet": [1, 2]}, {})
        wire = pack_status(status)
        self.assertTrue(wire.startswith(b"S1"))
        out = unpack_status(wire)
        self.assertEqual(
            out, [True, False, 1234, None, {"servername": "test", "telnet": [1, 2]}, {}]
        )

    def test_status_rejects_pickle(self):
        blob = pickle.dumps((True, True, 1, 2, {}, {}), pickle.HIGHEST_PROTOCOL)
        with self.assertRaises(ValueError):
            unpack_status(blob)

    def test_launcher_args_roundtrip_json(self):
        # Start args are the twistd command line (list of str) for a (re)start...
        cmd = ["twistd", "--python=server.py", "--pidfile=server.pid"]
        wire = pack_launcher_args(cmd)
        self.assertTrue(wire.startswith(b"L1"))
        self.assertEqual(unpack_launcher_args(wire), cmd)
        # ...or an empty dict for control operations that carry no args.
        wire = pack_launcher_args({})
        self.assertEqual(unpack_launcher_args(wire), {})

    def test_launcher_args_rejects_pickle(self):
        blob = pickle.dumps(["twistd", "--python=server.py"], pickle.HIGHEST_PROTOCOL)
        with self.assertRaises(ValueError):
            unpack_launcher_args(blob)

    def test_admin_sync_with_many_sessions_roundtrips(self):
        # A PSYNC/PCONNSYNC resync carries one sessiondata entry per session;
        # it must not be rejected by a fixed key cap in either direction.
        sessiondata = {sid: {"flag": sid} for sid in range(200)}
        wire = pack_admin_message(1, {"operation": "X", "sessiondata": sessiondata})
        self.assertTrue(wire.startswith(b"A1"))
        sessid, out = unpack_admin_message(wire)
        self.assertEqual(sessid, 1)
        self.assertEqual(len(out["sessiondata"]), 200)
        self.assertEqual(out["sessiondata"][5], {"flag": 5})


class TestNonStringKeysOnTheWire(SimpleTestCase):
    """Output with a dict key that is not a str reaches the player, converted.

    A frame that failed here was dropped whole, and with it whatever the player was
    about to read. Inbound data and the strict type rules are unchanged.
    """

    def setUp(self):
        super().setUp()
        # The warning is rate limited per process; each test starts fresh.
        patcher = mock.patch.object(amp_serde, "_last_coerced_key_warning", float("-inf"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_int_keys_are_sent_as_json_writes_them(self):
        wire = pack_session_message(3, {"panel": [{"rows": {1: "a", 2: "b"}}]})

        _sessid, out = unpack_session_message(wire)

        self.assertEqual(out["panel"][0]["rows"], {"1": "a", "2": "b"})

    def test_bool_none_and_float_keys_read_as_json_writes_them(self):
        wire = pack_session_message(3, {"x": {True: 1, None: 2, 2.5: 3}})

        _sessid, out = unpack_session_message(wire)

        self.assertEqual(out["x"], {"true": 1, "null": 2, "2.5": 3})

    def test_multicast_frames_are_converted_too(self):
        wire = pack_multicast_message([1, 2], {"x": {7: "seven"}})

        _sessids, out = unpack_multicast_message(wire)

        self.assertEqual(out, {"x": {"7": "seven"}})

    def test_two_keys_that_read_the_same_are_refused_not_merged(self):
        for payload in ({1: "a", "1": "b"}, {"1": "a", 1: "b"}):
            with self.subTest(payload=payload), self.assertRaises(TypeError):
                pack_session_message(3, {"x": payload})

    def test_a_key_json_cannot_write_is_still_refused(self):
        with self.assertRaises(TypeError):
            pack_session_message(3, {"x": {(1, 2): "a"}})
        with self.assertRaises(ValueError):
            pack_session_message(3, {"x": {float("nan"): "a"}})

    def test_the_strict_default_is_unchanged(self):
        with self.assertRaises(TypeError):
            sanitize_session_kwargs({"x": {1: "a"}})
        with self.assertRaises(TypeError):
            sanitize_session_kwargs({"x": {1: "a"}}, enforce_limits=False)

    def test_the_producer_is_named_once_not_once_per_frame(self):
        with mock.patch("evennia.utils.logger.log_warn") as warn:
            pack_session_message(3, {"shell_state": {5: "a"}, "text": "hi"})
            pack_session_message(3, {"shell_state": {6: "b"}})

        warn.assert_called_once()
        message = warn.call_args.args[0]
        self.assertIn("int 5", message)
        self.assertIn("shell_state", message)

    def test_it_warns_again_after_the_interval(self):
        with (
            mock.patch("evennia.utils.logger.log_warn") as warn,
            mock.patch.object(amp_serde.time, "monotonic", side_effect=[1000.0, 1000.0 + 601.0]),
        ):
            pack_session_message(3, {"x": {1: "a"}})
            pack_session_message(3, {"x": {1: "a"}})

        self.assertEqual(warn.call_count, 2)

    def test_a_failing_logger_does_not_cost_the_frame(self):
        with mock.patch("evennia.utils.logger.log_warn", side_effect=RuntimeError("log down")):
            wire = pack_session_message(3, {"x": {1: "a"}})

        self.assertEqual(unpack_session_message(wire)[1], {"x": {"1": "a"}})

    def test_frames_with_only_str_keys_never_warn(self):
        with mock.patch("evennia.utils.logger.log_warn") as warn:
            pack_session_message(3, {"x": {"1": "a"}, "text": "hi"})

        warn.assert_not_called()
