"""
Tests for secure AMP session serialization.
"""

import pickle

from django.test import override_settings

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

    def test_rejects_pickle_by_default(self):
        blob = pickle.dumps((1, {"text": "x"}), pickle.HIGHEST_PROTOCOL)
        with self.assertRaises(ValueError):
            unpack_session_message(blob)

    @override_settings(AMP_SESSION_ACCEPT_LEGACY_PICKLE=True)
    def test_legacy_pickle_when_explicitly_enabled(self):
        blob = pickle.dumps((2, {"text": "legacy"}), pickle.HIGHEST_PROTOCOL)
        sessid, out = unpack_session_message(blob)
        self.assertEqual(sessid, 2)
        self.assertEqual(out["text"], "legacy")

    def test_rejects_unsafe_types(self):
        with self.assertRaises(TypeError):
            sanitize_session_kwargs({"obj": self.char1})
