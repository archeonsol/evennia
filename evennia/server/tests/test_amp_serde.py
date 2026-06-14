"""
Tests for secure AMP session serialization.
"""

import pickle

from evennia.server.amp_serde import (pack_admin_message, pack_session_message,
                                      sanitize_session_kwargs,
                                      unpack_admin_message,
                                      unpack_session_message)
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
