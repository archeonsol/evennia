"""WebSocket message limits apply while fragments are accumulated."""

from unittest import TestCase
from unittest.mock import Mock

from django.test import override_settings
from wsproto.events import BytesMessage, TextMessage

from evennia.server.portal.webclient import WebSocketClient
from evennia.server.portal.wire_formats.azaban import AzabanFormat


class TestIncomingMessageLimits(TestCase):
    """The codec's byte budget also bounds unfinished messages."""

    def setUp(self):
        """Use the actual protocol accumulator with socket effects observed."""
        self.protocol = WebSocketClient()
        self.protocol.wire_format = AzabanFormat()
        self.protocol.onMessage = Mock()
        self.protocol.sendClose = Mock()
        self.protocol._lose_connection = Mock()

    @override_settings(AZABAN_MAX_INCOMING_BYTES=8)
    def test_multibyte_fragments_fail_before_message_completion(self):
        """Two Unicode codepoints can consume the complete byte budget."""
        self.protocol._collect_message(TextMessage(data="🙂", message_finished=False))
        self.protocol._collect_message(TextMessage(data="🙂", message_finished=False))
        self.protocol.sendClose.assert_not_called()
        self.protocol._collect_message(TextMessage(data="x", message_finished=False))
        self.protocol.sendClose.assert_called_once()
        self.assertEqual(self.protocol.sendClose.call_args.args[0], 1009)
        self.protocol._lose_connection.assert_called_once()
        self.protocol.onMessage.assert_not_called()
        self.assertEqual(self.protocol._msg_parts, [])

    @override_settings(AZABAN_MAX_INCOMING_BYTES=8)
    def test_exact_boundary_and_new_message_reset(self):
        """Completed messages release the accumulation budget."""
        for _ in range(2):
            self.protocol._collect_message(TextMessage(data="🙂🙂", message_finished=True))
        self.assertEqual(self.protocol.onMessage.call_count, 2)
        self.protocol.sendClose.assert_not_called()

    @override_settings(AZABAN_MAX_INCOMING_BYTES=8)
    def test_binary_messages_share_the_byte_boundary(self):
        """A binary frame cannot bypass the selected codec's budget."""
        self.protocol._collect_message(BytesMessage(data=b"a" * 9, message_finished=True))
        self.protocol.sendClose.assert_called_once()
        self.protocol.onMessage.assert_not_called()

    @override_settings(AZABAN_MAX_INCOMING_BYTES=8)
    def test_decoder_uses_the_same_message_budget(self):
        """Direct codec callers retain the same pre-parse protection."""
        self.assertIsNone(AzabanFormat().decode_incoming(b'{"t": "websocket_close"}', False))
