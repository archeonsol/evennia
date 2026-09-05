"""Session authority tests without account or database fixtures."""

from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase

from evennia.server.bus_sessions import SessionReconciler


class Handler(dict):
    """Minimal session creation and cleanup boundary."""

    def portal_connect(self, data):
        """Create a session from protocol-authenticated data."""
        self[data["sessid"]] = SimpleNamespace(**data)

    def portal_disconnect(self, session):
        """Remove exactly the live session."""
        del self[session.sessid]


class TestSessionReconciler(TestCase):
    """Falsify stale authentication and session replacement assumptions."""

    def setUp(self):
        """Supply one freshly authenticated socket."""
        self.handler = Handler()
        self.reconciler = SessionReconciler(self.handler)
        self.data = {
            1: {
                "sessid": 1,
                "_socket_id": "socket-a",
                "_protocol_auth": {"uid": 42, "logged_in": True},
                "uid": 999,
                "logged_in": True,
                "bid": 17,
                "protocol_flags": {"ANSI": True},
                "address": "local",
            }
        }

    def test_retains_objects_and_server_authentication(self):
        """A stale Portal mirror cannot undo logout or replace runtime state."""
        self.reconciler.reconcile(self.data, "portal-a")
        session = self.handler[1]
        session.menu = object()
        session.uid = None
        session.logged_in = False
        session.bid = None
        session.protocol_flags["ANSI"] = False
        self.reconciler.reconcile(self.data, "portal-a")
        self.assertIs(self.handler[1], session)
        self.assertIsNone(session.uid)
        self.assertFalse(session.logged_in)
        self.assertFalse(session.protocol_flags["ANSI"])

    def test_new_socket_uses_protocol_auth_not_mirror(self):
        """HTTP/SSH authentication survives without trusting mirrored auth."""
        self.reconciler.reconcile(self.data, "portal-a")
        self.assertEqual(self.handler[1].uid, 42)
        self.assertIsNone(self.handler[1].bid)

    def test_reused_number_and_portal_restart_replace_session(self):
        """Socket identity includes both process and socket incarnation."""
        self.reconciler.reconcile(self.data, "portal-a")
        old = self.handler[1]
        self.data[1]["_socket_id"] = "socket-b"
        self.reconciler.reconcile(self.data, "portal-a")
        self.assertIsNot(self.handler[1], old)
        old = self.handler[1]
        self.reconciler.reconcile(self.data, "portal-b")
        self.assertIsNot(self.handler[1], old)

    def test_missing_socket_disconnects(self):
        """Confirmed membership removes absent sessions normally."""
        self.reconciler.reconcile(self.data, "portal-a")
        self.reconciler.reconcile({}, "portal-a")
        self.assertFalse(self.handler)

    def test_server_disconnect_is_not_resurrected(self):
        """A lost disconnect publication cannot restore original socket auth."""
        self.reconciler.reconcile(self.data, "portal-a")
        self.handler.portal_disconnect(self.handler[1])
        self.reconciler.reconcile(self.data, "portal-a")
        self.assertFalse(self.handler)
        self.assertEqual(self.reconciler.server_state()["closed"], {1: "socket-a"})

    def test_changed_capability_applies_without_resetting_server_flag(self):
        """Client changes win only where the Portal metadata changed."""
        self.reconciler.reconcile(self.data, "portal-a")
        self.handler[1].protocol_flags["ANSI"] = False
        changed = deepcopy(self.data)
        changed[1]["protocol_flags"]["CLIENT_NARRATIVE"] = False
        self.reconciler.reconcile(changed, "portal-a")
        self.assertEqual(self.handler[1].protocol_flags, {"ANSI": False, "CLIENT_NARRATIVE": False})
