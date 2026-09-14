"""Launcher control operations must reach the Server over the bus in use."""

import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from evennia.server.bus_result import PublicationResult
from evennia.server.portal import amp, launcher_handlers
from evennia.server.redis_bus import RedisPortalBus


class TestLauncherLifecycleRouting(TestCase):
    """Publish launcher lifecycle frames instead of fanning out to no peer."""

    def setUp(self):
        """Build a Portal whose only Server link is the Redis bus."""
        self.factory = SimpleNamespace(
            broadcasts=[],
            server_connection=None,
            server_connect_callbacks=[],
            launcher_connection=None,
            disconnect_callbacks={},
            portal=None,
        )
        self.portal = SimpleNamespace(
            server_twistd_cmd=["server"],
            server_restart_mode=None,
            server_process_id=os.getpid(),
            _lifecycle_unconfirmed=False,
            shutdown=Mock(),
        )
        self.factory.portal = self.portal
        self.bus = RedisPortalBus(self.portal, factory=self.factory)
        self.portal.server_bus = self.bus
        self.bus._handshake.state = "ready"
        self.bus._handshake.pair = [["portal", "p"], ["server", "s"]]
        self.bus._transport.online = True
        self.bus._published_ready = True
        self.bus._transport.publish = Mock(return_value=PublicationResult())
        self.factory.server_connection = self.bus

        # the launcher IPC hands the handlers an AMP protocol that was never
        # connected to anything; only its local bookkeeping is usable.
        self.protocol = SimpleNamespace(
            factory=self.factory,
            get_status=Mock(return_value=(True, True, 1, 2, None, None)),
            send_Status2Launcher=Mock(),
            stop_server=Mock(),
            start_server=Mock(),
            wait_for_server_connect=Mock(),
        )

    def published(self):
        """Return the admin operation of the single published frame."""
        self.bus._transport.publish.assert_called_once()
        _stream, command, packed = self.bus._transport.publish.call_args[0]
        self.assertEqual(command, b"AdminPortal2Server")
        return amp.loads_admin(packed)[1]["operation"]

    def test_reload_publishes_over_the_bus(self):
        """`evennia reload` must publish SRELOAD, not fan out to no peer."""
        launcher_handlers.receive_launcher_command(self.protocol, amp.SRELOAD, b"")
        self.assertEqual(self.published(), amp.SRELOAD)
        self.protocol.stop_server.assert_not_called()

    def test_every_lifecycle_operation_publishes(self):
        """No launcher lifecycle path may resolve to the disconnected stub."""
        # a Portal shutdown stops the Server first, so it publishes SSHUTD
        expected = {
            amp.SRELOAD: amp.SRELOAD,
            amp.SRESET: amp.SRESET,
            amp.SSHUTD: amp.SSHUTD,
            amp.PSHUTD: amp.SSHUTD,
        }
        for operation, published in expected.items():
            with self.subTest(operation=operation):
                self.bus._transport.publish.reset_mock()
                self.portal.server_restart_mode = None
                launcher_handlers.receive_launcher_command(self.protocol, operation, b"")
                self.assertEqual(self.published(), published)
                self.protocol.stop_server.assert_not_called()

    def test_unpublished_reload_is_not_recorded_as_applied(self):
        """A rejected frame must not leave the Portal expecting a restart."""
        self.bus._published_ready = False
        launcher_handlers.receive_launcher_command(self.protocol, amp.SRELOAD, b"")
        self.bus._transport.publish.assert_not_called()
        self.assertIsNone(self.portal.server_restart_mode)
        self.assertFalse(self.portal._lifecycle_unconfirmed)

    def test_cold_start_still_launches_the_server_process(self):
        """With no Server running the launcher must still spawn one locally."""
        self.protocol.get_status.return_value = (True, False, 1, None, None, None)
        launcher_handlers.receive_launcher_command(
            self.protocol, amp.SSTART, amp.dumps_launcher_args(["server"])
        )
        self.protocol.wait_for_server_connect.assert_called_once()
        self.assertEqual(self.protocol.start_server.call_count, 1)
