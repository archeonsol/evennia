"""
Integration tests for the redis Streams Portal<->Server bus (plain XADD/XREAD).

Uses fakeredis so CI does not need a live redis daemon.
"""

import time
from unittest.mock import MagicMock, patch

import fakeredis
from django.test import SimpleTestCase, TestCase, override_settings

import evennia
from evennia.server import ipc_schema, redis_bus, session
from evennia.server.portal import amp, amp_server
from evennia.server.portal.portalsessionhandler import PortalSessionHandler
from evennia.server.portal.service import EvenniaPortalService
from evennia.server.redis_bus import (RedisPortalBus, RedisServerBus,
                                      _PidTransport, _RedisTransport)
from evennia.server.service import EvenniaServerService
from evennia.server.sessionhandler import ServerSessionHandler

_BUS_SETTINGS = {
    "SERVER_PORTAL_BUS": "redis",
    "REDIS_BUS_URL": "redis://127.0.0.1:6379/15",
    "REDIS_BUS_PREFIX": "evennia:testbus",
    "SERVER_WORKER_ID": "0",
}


def _sync_call_from_thread(fn, *args, **kwargs):
    """Run frame dispatch immediately (tests have no reactor thread)."""
    return fn(*args, **kwargs)


def _drain_bus(seconds=0.15):
    """Let redis reader threads pick up published frames."""
    time.sleep(seconds)


@override_settings(**_BUS_SETTINGS)
@patch("evennia.utils.clock.call_from_thread", _sync_call_from_thread)
class TestRedisBus(TestCase):
    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis(decode_responses=False)
        self.redis_patcher = patch("redis.Redis.from_url", return_value=self.fake_redis)
        self.redis_patcher.start()

        # This test overwrites process-global evennia services/handlers. Restore
        # them so later tests don't inherit this test's instances/mocks.
        _globals = (
            "EVENNIA_SERVER_SERVICE",
            "SERVER_SESSION_HANDLER",
            "EVENNIA_PORTAL_SERVICE",
            "PORTAL_SESSION_HANDLER",
        )
        _saved = {name: getattr(evennia, name, None) for name in _globals}
        self.addCleanup(lambda: [setattr(evennia, n, v) for n, v in _saved.items()])

        self.server = EvenniaServerService()
        self.server.run_initial_setup = MagicMock()
        evennia.EVENNIA_SERVER_SERVICE = self.server
        evennia.SERVER_SESSION_HANDLER = ServerSessionHandler()
        evennia.SERVER_SESSION_HANDLER.data_in = MagicMock()
        evennia.SERVER_SESSION_HANDLER.data_out = MagicMock()
        evennia.SERVER_SESSION_HANDLER.portal_disconnect_all = MagicMock()
        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync = MagicMock()

        self.session = MagicMock()
        self.session.sessid = 1
        evennia.SERVER_SESSION_HANDLER[1] = self.session

        self.portal = EvenniaPortalService()
        evennia.EVENNIA_PORTAL_SERVICE = self.portal
        self.portalsession = session.Session()
        self.portalsession.sessid = 1
        evennia.PORTAL_SESSION_HANDLER = PortalSessionHandler()
        evennia.PORTAL_SESSION_HANDLER[1] = self.portalsession
        evennia.PORTAL_SESSION_HANDLER.data_in = MagicMock()
        evennia.PORTAL_SESSION_HANDLER.data_out = MagicMock()
        evennia.PORTAL_SESSION_HANDLER.get_all_sync_data = MagicMock(return_value=[])
        evennia.PORTAL_SESSION_HANDLER.at_server_connection = MagicMock()

        self.amp_factory = amp_server.AMPServerFactory(self.portal)
        self.server_bus = RedisServerBus(self.server)
        self.server.portal_bus = self.server_bus
        self.portal_bus = RedisPortalBus(self.portal, factory=self.amp_factory)
        self.portal.server_bus = self.portal_bus
        self.amp_factory.server_connection = self.portal_bus

        # Match production lifecycle: the Portal is subscribed to s2p before the
        # Server boots and announces itself via PSYNC. Starting the server bus
        # first would publish PSYNC before the portal reader exists and the
        # ``$`` cursor would skip it. Let the portal reader reach its first
        # blocking xread before the server publishes.
        self.portal_bus.start_bus()
        time.sleep(0.05)
        self.server_bus.start_bus()

    def tearDown(self):
        try:
            self.server_bus.stop_bus()
        except Exception:
            pass
        try:
            self.portal_bus.stop_bus()
        except Exception:
            pass
        self.redis_patcher.stop()

    def test_stream_topology(self):
        prefix = _BUS_SETTINGS["REDIS_BUS_PREFIX"]
        self.assertEqual(self.server_bus._send_stream, f"{prefix}:s2p")
        self.assertEqual(self.server_bus._read_stream, f"{prefix}:p2s:0")
        self.assertEqual(self.portal_bus._send_stream, f"{prefix}:p2s:0")
        self.assertEqual(self.portal_bus._read_stream, f"{prefix}:s2p")

    def test_msgportal2server_roundtrip(self):
        self.portal_bus.send_MsgPortal2Server(self.portalsession, text={"foo": "bar"})
        _drain_bus()
        evennia.SERVER_SESSION_HANDLER.data_in.assert_called_with(self.session, text={"foo": "bar"})

    def test_msgserver2portal_roundtrip(self):
        self.server_bus.send_MsgServer2Portal(self.session, text={"hello": "world"})
        _drain_bus()
        evennia.PORTAL_SESSION_HANDLER.data_out.assert_called_with(
            self.portalsession, text={"hello": "world"}
        )

    def test_psync_on_server_start(self):
        _drain_bus()
        self.assertIsNotNone(self.portal.server_process_id)
        evennia.PORTAL_SESSION_HANDLER.at_server_connection.assert_called()

    def test_admin_pdisconnall(self):
        self.portal_bus.send_AdminPortal2Server(amp.DUMMYSESSION, operation=amp.PDISCONNALL)
        _drain_bus()
        evennia.SERVER_SESSION_HANDLER.portal_disconnect_all.assert_called()

    def test_admin_ssync_on_portal(self):
        evennia.PORTAL_SESSION_HANDLER.server_session_sync = MagicMock()
        self.server_bus.send_AdminServer2Portal(
            amp.DUMMYSESSION,
            operation=amp.SSYNC,
            sessiondata=[{"sessid": 1}],
            clean=True,
        )
        _drain_bus()
        evennia.PORTAL_SESSION_HANDLER.server_session_sync.assert_called_once_with(
            [{"sessid": 1}], True
        )

    def test_ipc_schema_session_wire(self):
        env = ipc_schema.SessionEnvelope(sessid=1, kwargs={"text": [["hi"], {}]})
        wire = env.to_wire()
        self.server_bus._on_frame(b"MsgPortal2Server", wire)
        evennia.SERVER_SESSION_HANDLER.data_in.assert_called_with(self.session, text=[["hi"], {}])

    def test_pid_transport_disconnect(self):
        transport = _PidTransport(self.portal)
        self.portal.server_process_id = 999999999
        self.assertFalse(transport.connected)

    def test_pid_transport_connected(self):
        import os

        transport = _PidTransport(self.portal)
        self.portal.server_process_id = os.getpid()
        self.assertTrue(transport.connected)


@override_settings(**_BUS_SETTINGS)
class TestRedisBusSerdeLimits(SimpleTestCase):
    """enforce_limits asymmetry: trusted Server output vs untrusted Portal input."""

    def test_trusted_large_server_output_passes(self):
        big = "x" * 50000
        env = ipc_schema.SessionEnvelope(sessid=1, kwargs={"text": [[big], {}]})
        wire = env.to_wire()
        sessid, kwargs = ipc_schema.parse_session(wire, enforce_limits=False)
        self.assertEqual(sessid, 1)
        self.assertIn("text", kwargs)

    def test_untrusted_oversized_portal_input_rejected(self):
        big = "x" * 70000
        env = ipc_schema.SessionEnvelope(sessid=1, kwargs={"text": [[big], {}]})
        wire = env.to_wire()
        with self.assertRaises(ValueError):
            ipc_schema.parse_session(wire, enforce_limits=True)

    def test_parse_admin_returns_wire_chr(self):
        env = ipc_schema.AdminEnvelope(
            sessid=0, operation=ipc_schema.AdminOperation.PSYNC, kwargs={"spid": 1}
        )
        wire = env.to_wire()
        sessid, operation, kwargs = ipc_schema.parse_admin(wire)
        self.assertEqual(operation, amp.PSYNC)
        self.assertEqual(sessid, 0)
        self.assertEqual(kwargs.get("spid"), 1)


@override_settings(**_BUS_SETTINGS)
class TestRedisTransportQueueBound(SimpleTestCase):
    """Publish queue is bounded: overflow drops the newest frame, logs once per stall."""

    @patch.object(redis_bus, "_MAX_QUEUE", 5)
    @patch.object(redis_bus.logger, "log_err")
    def test_publish_queue_bounded_drops_newest_logs_once(self, mock_log_err):
        # writer thread never started, so nothing drains the queue
        transport = _RedisTransport("evennia:testbus:s2p", lambda *a: None)
        for i in range(20):
            transport.publish("stream", b"MsgPortal2Server", str(i).encode())
        # capped at _MAX_QUEUE; the first 5 frames are kept, the newer 15 dropped
        self.assertEqual(transport._q.qsize(), 5)
        # one log for the whole stall episode, not one per dropped frame
        self.assertEqual(mock_log_err.call_count, 1)
