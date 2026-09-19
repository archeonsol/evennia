"""Startup publication and intentional shutdown retain distinct input gates."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import evennia
from evennia.server.bus_result import PublicationResult, TransportUnavailable
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
        result = self.bus.callRemote(
            amp.MsgServer2Portal, packed_data=b"startup output"
        )
        self.assertTrue(result.admitted)
        self.bus._transport.publish.assert_called_once()

    def test_grouped_startup_output_is_also_admitted(self):
        result = self.bus.callRemote(
            amp.MsgServer2PortalMany, packed_data=b"startup grouped output"
        )
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


class TestBotSessionRecovery(TestCase):
    """A bot session request must survive a generation change during startup."""

    def setUp(self):
        """Publish bot session requests through a bus the test settles."""
        from evennia.server.sessionhandler import ServerSessionHandler

        self.handler = ServerSessionHandler()
        self.results = []
        self.frames = []

        def send(_session, operation="", **kwargs):
            self.frames.append((operation, kwargs))
            result = PublicationResult()
            self.results.append(result)
            return result

        self.service = SimpleNamespace(
            portal_bus=SimpleNamespace(send_AdminServer2Portal=send)
        )

    def start(self, uid=7):
        """Request one bot session the way a starting Bot account does."""
        with patch.object(evennia, "EVENNIA_SERVER_SERVICE", self.service, create=True):
            return self.handler.start_bot_session("path.to.Factory", {"uid": uid})

    def retry(self):
        """Re-request whatever the bus never managed to publish."""
        with patch.object(evennia, "EVENNIA_SERVER_SERVICE", self.service, create=True):
            self.handler.retry_pending_bot_sessions()

    def test_request_publishes_a_connect_operation(self):
        """The request must reach the Portal as a bot connect frame."""
        self.start()
        self.assertEqual(len(self.frames), 1)
        operation, kwargs = self.frames[0]
        self.assertEqual(operation, amp.SCONN)
        self.assertEqual(kwargs["protocol_path"], "path.to.Factory")

    def test_discarded_request_is_republished_once_ready(self):
        """A frame dropped with the old generation must be re-requested."""
        self.start()
        self.results[0].fail(TransportUnavailable("connection generation replaced"))
        self.retry()
        self.assertEqual(len(self.frames), 2)
        self.assertEqual(self.frames[1][0], amp.SCONN)

    def test_published_request_is_not_republished(self):
        """A landed request must not start the bot a second time."""
        self.start()
        self.results[0].succeed("1-0")
        self.retry()
        self.assertEqual(len(self.frames), 1)

    def test_live_bot_session_cancels_a_pending_request(self):
        """A bot that reconnected on its own must not be connected twice."""
        self.start(uid=7)
        self.results[0].fail(TransportUnavailable("connection generation replaced"))
        self.handler[1] = SimpleNamespace(uid=7, logged_in=True)
        self.retry()
        self.assertEqual(len(self.frames), 1)
        self.assertFalse(self.handler._pending_bot_sessions)

    def test_bus_readiness_drives_the_retry(self):
        """Nothing else re-requests a bot session, so the bus must."""
        with patch.object(evennia, "SERVER_SESSION_HANDLER", self.handler, create=True):
            bus = RedisServerBus(SimpleNamespace())
        self.start()
        self.results[0].fail(TransportUnavailable("connection generation replaced"))
        with patch.object(evennia, "EVENNIA_SERVER_SERVICE", self.service, create=True):
            bus._ready()
        self.assertEqual(len(self.frames), 2)
