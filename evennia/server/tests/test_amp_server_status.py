"""Tests for AMPServerProtocol status reporting."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase


class AMPServerGetStatusTest(SimpleTestCase):
    def _protocol(self, portal, server_connected=False):
        from evennia.server.portal.amp_server import AMPServerProtocol

        factory = MagicMock(portal=portal)
        if server_connected:
            transport = MagicMock()
            transport.connected = True
            factory.server_connection = MagicMock(transport=transport)
        else:
            factory.server_connection = None
        protocol = AMPServerProtocol()
        protocol.factory = factory
        return protocol

    def test_portal_live_when_running(self):
        portal = MagicMock()
        portal.running = True
        portal.shutdown_complete = False
        portal.get_info_dict.return_value = {}
        portal.server_info_dict = {}
        portal.server_process_id = 42

        live, connected, _, spid, _, _ = self._protocol(portal).get_status()
        self.assertTrue(live)
        self.assertFalse(connected)
        self.assertEqual(spid, 42)

    def test_portal_not_live_after_shutdown_complete(self):
        portal = MagicMock()
        portal.running = True
        portal.shutdown_complete = True
        portal.get_info_dict.return_value = {}
        portal.server_info_dict = {}
        portal.server_process_id = 42

        live, _, _, _, _, _ = self._protocol(portal).get_status()
        self.assertFalse(live)

    def test_portal_live_when_ipc_ready_before_start_service(self):
        portal = MagicMock()
        portal.running = False
        portal._launcher_ipc_ready = True
        portal.shutdown_complete = False
        portal.get_info_dict.return_value = {}
        portal.server_info_dict = {}
        portal.server_process_id = 99

        live, _, _, spid, _, _ = self._protocol(portal).get_status()
        self.assertTrue(live)
        self.assertEqual(spid, 99)
