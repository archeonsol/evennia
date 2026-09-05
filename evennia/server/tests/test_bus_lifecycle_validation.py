"""Startup publication and intentional shutdown retain distinct input gates."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from evennia.server.bus_result import PublicationResult
from evennia.server.portal import amp
from evennia.server.redis_bus import RedisServerBus


class TestBusLifecycleAdmission(TestCase):
    """Exercise real bus gates while publication is deliberately delayed."""

    def setUp(self):
        """Keep startup negotiation ahead of its final publication callback."""
        self.bus = RedisServerBus(SimpleNamespace())
        self.bus._handshake.state = "ready"
        self.bus._handshake.pair = [["portal", "p"], ["server", "s"]]
        self.bus._transport.online = True
        self.bus._transport.publish = Mock(return_value=PublicationResult())

    def test_startup_output_waits_in_transport_before_ready_publication(self):
        """Asynchronous startup output remains admissible behind final ready."""
        self.assertFalse(self.bus.ready)
        result = self.bus.callRemote(amp.MsgServer2Portal, packed_data=b"startup output")
        self.assertTrue(result.admitted)
        self.bus._transport.publish.assert_called_once()

    def test_startup_allowance_excludes_lifecycle_actions(self):
        """Output admission does not authorize an early restart request."""
        for operation in (amp.SRELOAD, amp.SRESET, amp.SSHUTD, amp.PSHUTD):
            with self.subTest(operation=operation):
                result = self.bus.callRemote(
                    amp.AdminServer2Portal,
                    packed_data=amp.dumps_admin((0, {"operation": operation})),
                )
                self.assertFalse(result.admitted)
        self.bus._transport.publish.assert_not_called()

    def test_stopping_rejects_player_input_before_portal_notice(self):
        """Already arriving input cannot race the Portal's stopping notice."""
        self.bus._published_ready = True
        self.bus._on_frame = Mock()
        self.bus.begin_shutdown()
        self.bus._receive_frame(b"MsgPortal2Server", b"action")
        self.bus._on_frame.assert_not_called()
        self.assertFalse(self.bus.ready)
