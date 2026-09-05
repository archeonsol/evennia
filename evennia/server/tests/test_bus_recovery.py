"""Exercise recovery entrypoints without database or worker threads."""

from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import evennia
from evennia.server import ipc_handlers_server
from evennia.server.portal import amp, ipc_handlers_portal, portalsessionhandler
from evennia.server.portal.portalsessionhandler import PortalSessionHandler
from evennia.server.redis_bus import RedisPortalBus, RedisServerBus
from evennia.server.session import Session


class RecoveryHandler(dict):
    """Record lifecycle boundaries while retaining actual session objects."""

    def __init__(self):
        """Record creation separately from reload restoration."""
        super().__init__()
        self.connected = []
        self.restored = []

    def portal_connect(self, data):
        """Create the runtime object at the normal connection boundary."""
        self.connected.append(deepcopy(data))
        self[data["sessid"]] = SimpleNamespace(**data)

    def portal_disconnect(self, session):
        """Remove a socket through the disconnect boundary."""
        self.pop(session.sessid)

    def portal_sessions_sync(self, data, restart_mode):
        """Model process restoration without calling normal admission."""
        self.restored.append((deepcopy(data), restart_mode))
        self.update({sid: SimpleNamespace(**values) for sid, values in data.items()})


class TestRecoveryEntrypoints(TestCase):
    """Verify state transitions at Portal input and IPC boundaries."""

    def setUp(self):
        """Own global handlers and keep all Redis workers stopped."""
        self.portal_handler = PortalSessionHandler()
        self.server_handler = RecoveryHandler()
        self.portal = SimpleNamespace(server_restart_mode="reload", start_time=1)
        self.service = SimpleNamespace(
            run_init_hooks=Mock(), run_initial_setup=Mock(), get_info_dict=lambda: {}
        )
        for name, value in (
            ("PORTAL_SESSION_HANDLER", self.portal_handler),
            ("SERVER_SESSION_HANDLER", self.server_handler),
            ("EVENNIA_SERVER_SERVICE", self.service),
        ):
            context = patch.object(evennia, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.server_bus = RedisServerBus(self.service)
        self.service.portal_bus = self.server_bus
        self.portal_bus = RedisPortalBus(self.portal)
        self.portal.server_amp = self.portal_bus
        context = patch.object(evennia, "EVENNIA_PORTAL_SERVICE", self.portal)
        context.start()
        self.addCleanup(context.stop)
        context = patch.object(
            portalsessionhandler, "_CONNECTION_QUEUE", portalsessionhandler.deque()
        )
        context.start()
        self.addCleanup(context.stop)
        self.socket = self.socket_for(1)

    def socket_for(self, sid):
        """Use the real sync serializer on a simple protocol session."""
        session = Session()
        session.init_session("telnet", "127.0.0.1", self.portal_handler)
        session.sessid = sid
        session.server_connected = False
        self.portal_handler._ensure_bus_socket(session)
        self.portal_handler[sid] = session
        return session

    def test_unready_input_retains_declarations_but_never_actions(self):
        """No delayed callback can revive rejected text or editor saves."""
        with (
            patch.object(self.portal_bus, "send_MsgPortal2Server") as send,
            patch.object(portalsessionhandler.clock, "call_later") as later,
        ):
            self.portal_handler.data_in(
                self.socket,
                text="look",
                editor_save={"content": "must not execute"},
                azaban_hello={"caps": {"patches": True, "rendersNodes": True}},
                editor_client={"supported": True},
                narrative_client={"supported": False},
            )
        send.assert_not_called()
        later.assert_not_called()
        data = self.portal_handler.get_bus_sync_data()[1]
        self.assertTrue(data["protocol_flags"]["CLIENT_EDITOR"])
        self.assertFalse(data["protocol_flags"]["CLIENT_NARRATIVE"])
        self.assertEqual(
            data["protocol_flags"]["AZABAN_CAPS"], {"patches": True, "rendersNodes": True}
        )
        self.assertNotIn("editor_save", data)
        self.assertNotIn("text", data)
        self.assertGreater(self.portal_handler.bus_revision, 0)

    def test_telnet_metadata_reaches_matching_runtime_session(self):
        """Delayed NAWS negotiation changes flags without replacing auth."""
        data = self.socket.get_sync_data()
        self.server_bus._sessions.reconcile({1: data}, "portal")
        runtime = self.server_handler[1]
        runtime.uid = 42
        self.socket.server_connected = True
        self.socket.protocol_flags["SCREENWIDTH"] = {0: 132}
        with patch.object(self.portal_bus, "send_AdminPortal2Server") as send:
            self.portal_handler.sync(self.socket)
        sent = send.call_args.kwargs
        ipc_handlers_server.receive_adminportal2server(amp.dumps_admin((1, sent)))
        self.assertIs(self.server_handler[1], runtime)
        self.assertEqual(runtime.uid, 42)
        self.assertEqual(runtime.protocol_flags["SCREENWIDTH"], {0: 132})

    def test_pconn_application_ack_marks_socket_restorable(self):
        """An ordinary admitted socket survives the next startup as restored."""
        packed = amp.dumps_admin(
            (1, {"operation": amp.PCONN, "sessiondata": self.socket.get_sync_data()})
        )
        with patch.object(self.server_bus, "send_AdminServer2Portal") as send:
            ipc_handlers_server.receive_adminportal2server(packed)
        self.assertFalse(self.socket._bus_confirmed)
        ack = send.call_args.kwargs
        ipc_handlers_portal.receive_adminserver2portal(self.portal_bus, amp.dumps_admin((0, ack)))
        self.assertTrue(self.socket._bus_confirmed)
        self.assertTrue(self.socket.server_connected)
        self.assertTrue(self.socket.get_sync_data()["_server_confirmed"])
        self.assertEqual(len(self.server_handler.connected), 1)

    def test_old_connection_ack_does_not_confirm_replacement_socket(self):
        """Numeric session reuse cannot let an old acknowledgment grant readiness."""
        old_id = self.socket._bus_socket_id
        replacement = self.socket_for(1)
        self.portal_handler.apply_bus_state(
            {"sessions": {1: {"_socket_id": old_id, "uid": 99, "logged_in": True}}, "closed": {}}
        )
        self.assertFalse(replacement._bus_confirmed)
        self.assertIsNone(replacement.uid)

    def test_startup_restores_confirmed_and_connects_unadmitted_once(self):
        """A waiting pre-start socket still enters its normal connection flow."""
        self.socket._bus_confirmed = True
        self.socket.uid = 42
        self.socket.logged_in = True
        self.socket.bid = 77
        self.socket_for(2)
        self.server_bus._handshake.pair = [["portal", "epoch"], ["server", "epoch"]]
        payload = self.portal_bus._snapshot()[1]
        self.server_bus._apply_snapshot(payload)
        retained = self.server_handler[1]
        retained.menu = object()
        menu = retained.menu
        self.server_bus._apply_snapshot(payload)
        self.service.run_init_hooks.assert_called_once_with("reload")
        self.assertEqual(len(self.server_handler.restored), 1)
        self.assertEqual(set(self.server_handler.restored[0][0]), {1})
        self.assertEqual([data["sessid"] for data in self.server_handler.connected], [2])
        self.assertIs(self.server_handler[1], retained)
        self.assertIs(retained.menu, menu)
        self.assertEqual(retained.bid, 77)

    def test_partial_startup_failure_cannot_repeat_hooks(self):
        """Fresh discovery cannot rerun initialization after a partial failure."""
        self.server_bus._handshake.pair = [["portal", "epoch"], ["server", "epoch"]]
        self.service.run_init_hooks.side_effect = RuntimeError("init failed")
        payload = self.portal_bus._snapshot()[1]
        with self.assertRaisesRegex(RuntimeError, "init failed"):
            self.server_bus._apply_snapshot(payload)
        with self.assertRaisesRegex(RuntimeError, "process restart required"):
            self.server_bus._apply_snapshot(payload)
        self.service.run_init_hooks.assert_called_once()

    def test_restart_mode_consumed_once_per_server_process(self):
        """Transport recovery preserves a pending mode; new process consumes it."""
        self.portal_bus._handshake.pair = [["portal", "epoch"], ["server-1", "epoch"]]
        self.portal_bus._ready()
        self.assertIsNone(self.portal.server_restart_mode)
        self.portal.server_restart_mode = "reset"
        self.portal_bus._ready()
        self.assertEqual(self.portal.server_restart_mode, "reset")
        self.portal_bus._handshake.pair[1] = ["server-2", "new-epoch"]
        self.portal_bus._ready()
        self.assertIsNone(self.portal.server_restart_mode)

    def test_initial_setup_failure_never_starts_transport_on_retry(self):
        """An attempted setup is not a completed setup."""
        self.service.run_initial_setup.side_effect = RuntimeError("setup failed")
        with patch.object(self.server_bus._transport, "start") as start:
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                self.server_bus.start_bus()
            with self.assertRaisesRegex(RuntimeError, "process restart required"):
                self.server_bus.start_bus()
        start.assert_not_called()
        self.service.run_initial_setup.assert_called_once()
