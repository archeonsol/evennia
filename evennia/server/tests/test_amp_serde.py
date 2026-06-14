"""
Tests for secure AMP session serialization.
"""

import pickle

from evennia.server.amp_serde import (
    pack_admin_message,
    pack_launcher_args,
    pack_session_message,
    pack_status,
    sanitize_session_kwargs,
    unpack_admin_message,
    unpack_launcher_args,
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
