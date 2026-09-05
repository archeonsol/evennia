"""
Reload survival over the redis bus: Portal sessions stay; PSYNC reattaches Server.

These tests do not spawn full Portal/Server processes; they exercise the IPC +
session-handler contract that keeps live Portal sockets across ``@reload``.
"""

import time
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

import evennia
from evennia.server import session
from evennia.server.portal import amp, amp_server
from evennia.server.portal.portalsessionhandler import PortalSessionHandler
from evennia.server.portal.service import EvenniaPortalService
from evennia.server.redis_bus import RedisPortalBus, RedisServerBus
from evennia.server.service import EvenniaServerService
from evennia.server.sessionhandler import ServerSessionHandler
from evennia.server.tests.test_redis_bus import (
    _BUS_SETTINGS,
    _BusTestResources,
    _drain_bus,
)


@override_settings(**_BUS_SETTINGS)
class TestRedisReloadSurvival(TestCase):
    def setUp(self):
        self.resources = _BusTestResources()
        self.addCleanup(self.resources.close)
        self.fake_redis = self.resources.fake

        self.server = EvenniaServerService()
        self.server.run_initial_setup = MagicMock()
        self.server.run_init_hooks = MagicMock()
        evennia.EVENNIA_SERVER_SERVICE = self.server
        evennia.SERVER_SESSION_HANDLER = ServerSessionHandler()
        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync = MagicMock()
        evennia.SERVER_SESSION_HANDLER.data_in = MagicMock()

        self.session = MagicMock()
        self.session.sessid = 1
        evennia.SERVER_SESSION_HANDLER[1] = self.session

        self.portal = EvenniaPortalService()
        evennia.EVENNIA_PORTAL_SERVICE = self.portal
        self.portalsession = session.Session()
        self.portalsession.sessid = 1
        self.portalsession.protocol = MagicMock()
        self.portalsession.protocol.transport = MagicMock()
        self.portalsession.protocol.transport.connected = True
        evennia.PORTAL_SESSION_HANDLER = PortalSessionHandler()
        evennia.PORTAL_SESSION_HANDLER[1] = self.portalsession
        evennia.PORTAL_SESSION_HANDLER.get_all_sync_data = MagicMock(
            return_value=[{"sessid": 1, "uid": 42}]
        )
        evennia.PORTAL_SESSION_HANDLER.at_server_connection = MagicMock()
        evennia.PORTAL_SESSION_HANDLER.server_session_sync = MagicMock()

        self.amp_factory = amp_server.AMPServerFactory(self.portal)
        self.server_bus = RedisServerBus(self.server)
        self.resources.buses.append(self.server_bus)
        self.server.portal_bus = self.server_bus
        self.portal_bus = RedisPortalBus(self.portal, factory=self.amp_factory)
        self.resources.buses.append(self.portal_bus)
        self.portal.server_bus = self.portal_bus
        self.amp_factory.server_connection = self.portal_bus

        self.server_bus.start_bus()
        self.portal_bus.start_bus()
        _drain_bus()

    def test_initial_handshake_completed_during_setup(self):
        """The initial PSYNC must dispatch before the test body starts."""
        self.server.run_init_hooks.assert_called_once_with("shutdown")

    def test_portal_ws_session_stays_connected_through_reload_admin(self):
        """SRELOAD admin op must not tear down Portal-side protocol transports."""
        with patch.object(self.portal_bus, "wait_for_disconnect", MagicMock()):
            with patch.object(self.portal_bus, "stop_server", MagicMock()) as mock_stop:
                self.server_bus.send_AdminServer2Portal(amp.DUMMYSESSION, operation=amp.SRELOAD)
                _drain_bus()
                mock_stop.assert_called_once_with(mode="reload")
        self.assertTrue(self.portalsession.protocol.transport.connected)
        self.assertIn(1, evennia.PORTAL_SESSION_HANDLER)

    def test_azaban_ws_survives_reload_and_delivers_post_reload_text(self):
        """B3: asyncio WS session + azaban hello survives reload; output after PSYNC."""
        from evennia.narrative.rendernode import CLIENT_NARRATIVE_FLAG
        from evennia.server.portal.asyncio_transport import AsyncioTransportShim
        from evennia.server.portal.webclient import WebSocketClient
        from evennia.server.portal.wire_formats.azaban import AzabanFormat

        inner = MagicMock()
        inner.connected = True
        ws = WebSocketClient()
        ws.sessid = 1
        ws.protocol_flags = {
            CLIENT_NARRATIVE_FLAG: True,
            "AZABAN_CAPS": {"rendersNodes": True, "patches": True},
        }
        ws.wire_format = AzabanFormat()
        ws.transport = AsyncioTransportShim(inner)
        ws.sessionhandler = evennia.PORTAL_SESSION_HANDLER
        evennia.PORTAL_SESSION_HANDLER[1] = ws

        self.assertTrue(ws.protocol_flags.get(CLIENT_NARRATIVE_FLAG))

        outbound = []
        ws.sendEncoded = MagicMock(side_effect=lambda data, **kw: outbound.append(data))

        with patch.object(self.portal_bus, "wait_for_disconnect", MagicMock()):
            with patch.object(self.portal_bus, "stop_server", MagicMock()):
                self.server_bus.send_AdminServer2Portal(amp.DUMMYSESSION, operation=amp.SRELOAD)
                _drain_bus()

        self.assertFalse(ws.transport.disconnecting)
        self.assertIn(1, evennia.PORTAL_SESSION_HANDLER)

        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync.reset_mock()
        sessiondata = ws.get_sync_data()
        self.portal_bus.send_AdminPortal2Server(
            amp.DUMMYSESSION,
            operation=amp.PSYNC,
            server_restart_mode="reload",
            sessiondata=[sessiondata],
            portal_start_time=time.time(),
        )
        _drain_bus(0.3)
        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync.assert_called_once_with([sessiondata])
        synced = evennia.SERVER_SESSION_HANDLER.portal_sessions_sync.call_args.args[0][0]
        self.assertTrue(synced["protocol_flags"]["AZABAN_CAPS"]["patches"])

        outbound.clear()
        evennia.PORTAL_SESSION_HANDLER.data_out(ws, text=[["after reload"], {}])
        self.assertEqual(len(outbound), 1)
        self.assertIsInstance(outbound[0], dict)
        self.assertEqual(outbound[0]["t"], "render")
        self.assertEqual(len(outbound[0]["nodes"]), 1)
        node = outbound[0]["nodes"][0]
        self.assertEqual(node["schema"], "render.v1")
        self.assertEqual(node["kind"], "text")
        self.assertEqual(node["body"], "after reload")

    def test_server_start_psync_triggers_portal_handshake(self):
        """Server ``start_bus`` PSYNC must reach Portal and fire ``at_server_connection``."""
        evennia.PORTAL_SESSION_HANDLER.at_server_connection.reset_mock()
        self.server_bus.stop_bus()
        self.server_bus.start_bus()
        _drain_bus()
        evennia.PORTAL_SESSION_HANDLER.at_server_connection.assert_called()
        self.assertIsNotNone(self.portal.server_process_id)

    def test_psync_after_server_restart_resyncs_sessions(self):
        """Portal PSYNC payload after Server reconnect reattaches sessions on Server."""
        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync.reset_mock()
        self.portal_bus.send_AdminPortal2Server(
            amp.DUMMYSESSION,
            operation=amp.PSYNC,
            server_restart_mode="reload",
            sessiondata=[{"sessid": 1, "uid": 42}],
            portal_start_time=time.time(),
        )
        _drain_bus()
        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync.assert_called_once_with(
            [{"sessid": 1, "uid": 42}]
        )

    def test_server_output_after_psync_reaches_portal_session(self):
        """Post-reload output path: Server -> redis -> Portal session data_out."""
        evennia.PORTAL_SESSION_HANDLER.data_out = MagicMock()
        self.server_bus.send_MsgServer2Portal(self.session, text={"hello": "after_reload"})
        _drain_bus()
        evennia.PORTAL_SESSION_HANDLER.data_out.assert_called_with(
            self.portalsession, text={"hello": "after_reload"}
        )
