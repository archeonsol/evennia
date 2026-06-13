"""
Tests for secure AMP session serialization.
"""

import pickle

from evennia.server.amp_serde import (pack_session_message,
                                      sanitize_session_kwargs,
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
