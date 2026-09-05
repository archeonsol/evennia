"""Current capabilities are retained without retaining client actions."""

from types import SimpleNamespace
from unittest import TestCase

from evennia.server.client_negotiation import capability_flags, retain_capabilities


class TestClientNegotiation(TestCase):
    """Use the same declaration parser at Portal and Server."""

    def test_hello_sets_and_clears_flags(self):
        """Explicit false replaces earlier capability support."""
        session = SimpleNamespace(protocol_flags={})
        self.assertTrue(
            retain_capabilities(
                session, {"azaban_hello": [[], {"caps": {"rendersNodes": True, "patches": True}}]}
            )
        )
        self.assertTrue(session.protocol_flags["CLIENT_NARRATIVE"])
        retain_capabilities(session, {"azaban_hello": [[], {"caps": {}}]})
        self.assertFalse(session.protocol_flags["CLIENT_NARRATIVE"])
        self.assertEqual(session.protocol_flags["AZABAN_CAPS"], {})

    def test_actionable_oob_is_not_negotiation(self):
        """Editor saves and arbitrary named actions have no retained state."""
        session = SimpleNamespace(protocol_flags={})
        self.assertFalse(
            retain_capabilities(session, {"editor_save": [[], {"content": "do not store"}]})
        )
        self.assertEqual(session.protocol_flags, {})

    def test_invalid_caps_do_not_mutate_state(self):
        """Invalid capability shapes cannot masquerade as truthy booleans."""
        self.assertIsNone(capability_flags("azaban_hello", {"caps": []}))
        self.assertIsNone(capability_flags("azaban_hello", {"caps": {"patches": "yes"}}))
        self.assertIsNone(capability_flags("editor_client", {"supported": "false"}))
