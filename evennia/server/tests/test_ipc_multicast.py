"""Multicast chunking and the oversized-frame fallback share the bus limits."""

from unittest import TestCase
from unittest.mock import Mock, patch

from evennia.server import ipc_handlers_server
from evennia.server import redis_transport as transport_module
from evennia.server.portal import amp


class TestMulticastChunkLimits(TestCase):
    """Chunk cap and fallback threshold resolve through the limits object."""

    def _link(self):
        link = Mock()
        link.callRemote.side_effect = lambda command, packed_data: Mock()
        return link

    def test_sessions_chunk_by_the_resolved_multicast_cap(self):
        link = self._link()
        with patch.object(transport_module, "MULTICAST_MAX_SESSIDS", 2):
            ipc_handlers_server.send_msgserver2portal_many(link, [1, 2, 3, 4, 5], text="x")
        calls = link.callRemote.call_args_list
        self.assertEqual([call.args[0] for call in calls], [amp.MsgServer2PortalMany] * 3)
        chunks = [amp.loads_multicast(call.kwargs["packed_data"])[0] for call in calls]
        self.assertEqual(chunks, [[1, 2], [3, 4], [5]])

    def test_oversized_chunks_fall_back_to_per_session_frames(self):
        """Delivery degrades per session instead of dropping the rejected chunk."""
        link = self._link()
        with (
            patch.object(transport_module, "MULTICAST_MAX_SESSIDS", 2),
            patch.object(transport_module, "MAX_FRAME_BYTES", 1),
        ):
            ipc_handlers_server.send_msgserver2portal_many(link, [1, 2], text="x")
        commands = [call.args[0] for call in link.callRemote.call_args_list]
        self.assertEqual(commands, [amp.MsgServer2Portal, amp.MsgServer2Portal])
