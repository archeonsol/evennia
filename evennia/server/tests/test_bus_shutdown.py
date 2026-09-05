"""Application acknowledgments and lifecycle admission under delivery uncertainty."""

import asyncio
import time
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

import evennia
from evennia.server.bus_result import PublicationResult, TransportUnavailable
from evennia.server.portal import amp, amp_server, ipc_handlers_portal
from evennia.server.portal.portalsessionhandler import PortalSessionHandler
from evennia.server.redis_bus import RedisServerBus
from evennia.server.session import Session


class TestFinalSnapshot(IsolatedAsyncioTestCase):
    """Exercise waiter ordering without assuming publication implies application."""

    def setUp(self):
        """Use an actual bus with publication controlled by the test."""
        self.bus = RedisServerBus(SimpleNamespace())
        self.bus._handshake.state = "stopping"
        self.bus._handshake.pair = [["portal", "p"], ["server", "s"]]
        self.bus._shutdown_deadline = time.monotonic() + 1
        self.publication = PublicationResult()
        self.frames = []

        def publish(_session, **kwargs):
            self.frames.append(kwargs)
            return self.publication

        self.bus.send_AdminServer2Portal = publish

    def ack(self):
        """Deliver the exact Portal application confirmation."""
        snapshot_id = self.frames[-1]["snapshot_id"]
        self.bus._receive_frame(
            b"BusSnapshotAck", amp.dumps_admin((0, {"snapshot_id": snapshot_id}))
        )

    async def test_publication_does_not_complete_snapshot(self):
        """The waiter remains pending until Portal reports application."""
        task = asyncio.create_task(self.bus.sync_sessions({}))
        await asyncio.sleep(0)
        self.publication.succeed("1-0")
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.ack()
        await task
        self.assertFalse(self.bus._snapshot_waiters)

    async def test_teardown_does_not_consume_snapshot_deadline(self):
        """Slow healthy teardown leaves time for final Portal application."""
        self.bus._shutdown_deadline = None
        self.bus._handshake.state = "ready"
        self.bus._publish_control = Mock(return_value=PublicationResult())
        with patch(
            "evennia.server.redis_bus.time.monotonic",
            return_value=time.monotonic() - 10,
        ):
            self.bus.begin_shutdown()
        self.bus.begin_shutdown()
        self.bus._publish_control.assert_called_once()
        task = asyncio.create_task(self.bus.sync_sessions({}))
        await asyncio.sleep(0.01)
        self.ack()
        await task

    async def test_ack_can_arrive_before_publication_callback(self):
        """Register correlation before the frame can reach the peer."""
        task = asyncio.create_task(self.bus.sync_sessions({}))
        await asyncio.sleep(0)
        self.ack()
        await task
        self.publication.succeed("1-0")
        self.assertFalse(self.bus._snapshot_waiters)

    async def test_deadline_is_bounded_and_late_ack_is_ignored(self):
        """An unconfirmed final snapshot cannot hold process shutdown open."""
        self.bus._shutdown_deadline = time.monotonic() + 0.02
        with self.assertRaises(TimeoutError):
            await self.bus.sync_sessions({})
        self.ack()
        self.assertFalse(self.bus._snapshot_waiters)

    async def test_stopping_transport_failure_settles_waiter(self):
        """Stopping cannot prevent independent transport invalidation."""
        task = asyncio.create_task(self.bus.sync_sessions({}))
        await asyncio.sleep(0)
        self.bus._transport_failed("Redis failed")
        with self.assertRaises(TransportUnavailable):
            await task


class TestLifecyclePublication(TestCase):
    """Rejected and ambiguous requests do not arm successful lifecycle behavior."""

    def protocol(self, result):
        """Construct only the protocol boundaries used by stop_server."""
        protocol = SimpleNamespace(
            factory=SimpleNamespace(portal=SimpleNamespace(server_restart_mode=None)),
            send_AdminPortal2Server=Mock(return_value=result),
        )
        return protocol

    def test_mode_waits_for_successful_publication(self):
        """Admission is insufficient to report a successful reload request."""
        result = PublicationResult()
        protocol = self.protocol(result)
        amp_server.AMPServerProtocol.stop_server(protocol, "reload")
        self.assertIsNone(protocol.factory.portal.server_restart_mode)
        self.assertTrue(protocol.factory.portal._lifecycle_unconfirmed)
        result.succeed("1-0")
        self.assertEqual(protocol.factory.portal.server_restart_mode, "reload")
        self.assertFalse(protocol.factory.portal._lifecycle_unconfirmed)

    def test_rejection_does_not_arm_mode_or_watchdog_suppression(self):
        """A request that was never admitted changes no lifecycle intent."""
        result = PublicationResult.rejected(TransportUnavailable("full"))
        protocol = self.protocol(result)
        amp_server.AMPServerProtocol.stop_server(protocol, "reload")
        self.assertIsNone(protocol.factory.portal.server_restart_mode)
        self.assertFalse(getattr(protocol.factory.portal, "_lifecycle_unconfirmed", False))

    def test_uncertain_shutdown_inhibits_automatic_restart(self):
        """The crash watchdog must not undo an ambiguously applied shutdown."""
        from evennia.server.portal.service import EvenniaPortalService

        result = PublicationResult()
        protocol = self.protocol(result)
        portal = protocol.factory.portal
        portal.shutdown_complete = False
        portal.server_process_id = 123
        portal.server_twistd_cmd = ["server"]
        portal._last_server_autorestart = -100
        portal._launcher_amp_protocol = SimpleNamespace(start_server=Mock())
        amp_server.AMPServerProtocol.stop_server(protocol, "shutdown")
        result.fail(TransportUnavailable("reply lost"))
        with patch("evennia.server.redis_bus._pid_alive", return_value=False):
            EvenniaPortalService._maybe_restart_dead_server(portal)
        portal._launcher_amp_protocol.start_server.assert_not_called()

    def test_final_application_preserves_new_socket_and_negotiation(self):
        """Final Server state cannot overwrite a replacement socket or fresh caps."""
        handler = PortalSessionHandler()
        session = Session()
        session.init_session("websocket", "local", handler)
        session.sessid = 1
        handler[1] = session
        handler._ensure_bus_socket(session)
        session.protocol_flags["CLIENT_NARRATIVE"] = True
        payload = {
            1: {
                "_socket_id": session._bus_socket_id,
                "_portal_flags": {"CLIENT_NARRATIVE": False},
                "protocol_flags": {"CLIENT_NARRATIVE": False},
                "uid": 42,
                "logged_in": True,
            }
        }
        handler.apply_final_bus_state(payload)
        self.assertTrue(session.protocol_flags["CLIENT_NARRATIVE"])
        self.assertEqual(session.uid, 42)
        payload[1]["_socket_id"] = "old socket"
        payload[1]["uid"] = 999
        handler.apply_final_bus_state(payload)
        self.assertEqual(session.uid, 42)

    def test_failed_lifecycle_does_not_register_disconnect_callback(self):
        """A failed stop cannot leave a delayed process restart armed."""
        result = PublicationResult.rejected(TransportUnavailable("full"))
        link = SimpleNamespace(
            factory=SimpleNamespace(
                server_connection=None,
                portal=SimpleNamespace(server_twistd_cmd=["server"]),
            ),
            stop_server=Mock(return_value=result),
            wait_for_disconnect=Mock(),
            start_server=Mock(),
        )
        with patch.object(evennia, "PORTAL_SESSION_HANDLER", PortalSessionHandler()):
            ipc_handlers_portal.receive_adminserver2portal(
                link, amp.dumps_admin((0, {"operation": amp.SRELOAD}))
            )
        link.wait_for_disconnect.assert_not_called()
