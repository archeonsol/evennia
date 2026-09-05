"""Deterministic peer ordering and session snapshot confirmation tests."""

from unittest import TestCase

from evennia.server.bus_handshake import BusHandshake
from evennia.server.bus_result import PublicationResult, TransportUnavailable


class TestHandshake(TestCase):
    """Drive separate peers without threads or shared process globals."""

    def setUp(self):
        """Create peers and an explicitly drained wire."""
        self.now = 0
        self.wire = []
        self.revision = 0
        self.applied = []
        self.ready = []
        self.peers = {}
        for role in ("portal", "server"):
            self.peers[role] = BusHandshake(
                role,
                send=lambda frame, role=role: self.wire.append((role, frame)),
                snapshot=lambda: (self.revision, {"sessions": {}}),
                apply=lambda payload: self.applied.append(payload),
                state_snapshot=lambda: {"auth": "current"},
                apply_state=lambda payload: None,
                on_ready=lambda role=role: self.ready.append(role),
                on_unavailable=lambda: None,
                now=lambda: self.now,
            )

    def drain(self):
        """Deliver all emitted frames in publication order."""
        for _ in range(30):
            if not self.wire:
                return
            role, frame = self.wire.pop(0)
            self.peers["server" if role == "portal" else "portal"].receive(frame)
        self.fail("handshake did not converge")

    def connect(self):
        """Complete fresh discovery."""
        self.peers["portal"].tick()
        self.drain()
        self.assertEqual([p.state for p in self.peers.values()], ["ready", "ready"])

    def test_both_orders_and_ready_only_after_application(self):
        """Neither peer needs a retained startup publication."""
        self.peers["server"].tick()
        self.assertFalse(self.wire)
        self.connect()
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.ready, ["server", "portal"])

    def test_heartbeat_does_not_reapply_snapshot(self):
        """Healthy discovery refreshes liveness without session hooks."""
        self.connect()
        for _ in range(5):
            self.now += 1
            self.peers["portal"].tick()
            self.drain()
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(len(self.ready), 2)

    def test_one_sided_reconnect(self):
        """A replaced transport identity forces fresh confirmation."""
        self.connect()
        self.peers["server"].disconnect()
        self.now += 1
        self.peers["portal"].tick()
        self.drain()
        self.assertEqual(len(self.applied), 2)
        self.assertEqual(self.peers["portal"].pair, self.peers["server"].pair)

    def test_stale_discovery_cannot_replace_ready_pair(self):
        """An old probe has no authority to change readiness."""
        self.peers["portal"].tick()
        old = self.wire[0][1]
        self.drain()
        pair = self.peers["server"].pair
        self.peers["server"].receive(old)
        self.drain()
        self.assertEqual(self.peers["server"].pair, pair)
        self.assertEqual(self.peers["server"].state, "ready")
        self.assertEqual(len(self.applied), 1)

    def test_membership_change_before_final_confirmation(self):
        """A socket replacement cannot receive stale authentication."""
        self.peers["portal"].tick()
        while self.wire and self.wire[0][1]["kind"] != "ready":
            role, frame = self.wire.pop(0)
            self.peers["server" if role == "portal" else "portal"].receive(frame)
        self.revision += 1
        self.drain()
        self.assertNotEqual(self.peers["portal"].state, "ready")
        self.now += 5
        self.peers["portal"].tick()
        self.drain()
        self.assertEqual(self.peers["portal"].state, "ready")

    def test_pending_exchange_not_overwritten_by_heartbeat(self):
        """A delayed snapshot has its full deadline to complete."""
        self.peers["portal"].tick()
        pending = list(self.wire)
        self.now += 2
        self.peers["portal"].tick()
        self.assertEqual(self.wire, pending)
        self.drain()
        self.assertEqual(self.peers["portal"].state, "ready")

    def test_peer_lease_expires(self):
        """A dead peer closes action admission without PID assumptions."""
        self.connect()
        self.now += 5
        for peer in self.peers.values():
            peer.tick()
            self.assertNotEqual(peer.state, "ready")

    def test_delayed_final_ack_cannot_complete_new_exchange(self):
        """Expired confirmation carries no authority over new discovery."""
        self.peers["portal"].tick()
        while self.wire[0][1]["kind"] != "ready":
            role, frame = self.wire.pop(0)
            self.peers["server" if role == "portal" else "portal"].receive(frame)
        old = self.wire.pop()[1]
        self.peers["portal"].disconnect()
        self.peers["portal"].tick()
        self.peers["portal"].receive(old)
        self.assertNotEqual(self.peers["portal"].state, "ready")
        self.drain()
        self.assertEqual(self.peers["portal"].state, "ready")

    def test_old_probe_cannot_overwrite_current_challenge(self):
        """A delayed discovery between offer and answer preserves live progress."""
        self.connect()
        self.now += 1
        self.peers["portal"].tick()
        role, current = self.wire.pop(0)
        self.peers["server"].receive(current)
        self.peers["server"].receive({"kind": "probe", "portal": ["old", "old"], "probe": "old"})
        self.drain()
        self.assertEqual(self.peers["portal"].state, "ready")
        self.assertEqual(len(self.applied), 1)

    def hold_final_publication(self):
        """Complete negotiation up to the Server's pending ready write."""
        result = PublicationResult()
        self.held_ready = None

        def send(frame):
            if frame["kind"] == "ready":
                self.held_ready = frame
                return result
            self.wire.append(("server", frame))
            return None

        self.peers["server"]._send = send
        self.peers["portal"].tick()
        self.drain()
        self.assertIsNotNone(self.held_ready)
        return result

    def test_ready_hook_waits_for_publication(self):
        """Admission alone cannot release Server startup output."""
        result = self.hold_final_publication()
        self.assertEqual(self.ready, [])
        self.assertNotEqual(self.peers["portal"].state, "ready")
        result.succeed("1-0")
        self.assertEqual(self.ready, ["server"])
        self.peers["portal"].receive(self.held_ready)
        self.assertEqual(self.ready, ["server", "portal"])

    def test_late_publication_cannot_open_replaced_generation(self):
        """A previous final-ready completion cannot reopen the current peer."""
        result = self.hold_final_publication()
        server = self.peers["server"]
        server.disconnect()
        server.state = "ready"
        server.pair = [["different", "portal"], server.identity]
        result.succeed("1-0")
        self.assertEqual(self.ready, [])

    def test_failed_ready_publication_never_fires_hook(self):
        """An ambiguous ready write invalidates the exchange."""
        result = self.hold_final_publication()
        result.fail(TransportUnavailable("reply lost"))
        self.assertEqual(self.ready, [])
        self.assertEqual(self.peers["server"].state, "disconnected")

    def test_stopping_peer_keeps_heartbeat_until_sync_deadline(self):
        """Healthy shutdown can use its fifth second without lease expiry."""
        self.connect()
        self.peers["server"].state = "stopping"
        pair = self.peers["server"].pair
        for instant in (1, 2, 3, 4, 4.5):
            self.now = instant
            for peer in self.peers.values():
                peer.tick()
            self.drain()
            self.assertEqual(self.peers["portal"].state, "ready")
            self.assertEqual(self.peers["server"].state, "stopping")
            self.assertEqual(self.peers["portal"].pair, pair)
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.ready, ["server", "portal"])

    def test_stopping_peer_rejects_fresh_snapshot_reconciliation(self):
        """A new Portal cannot replace sessions while Server finalizes them."""
        self.connect()
        server = self.peers["server"]
        pair = server.pair
        server.state = "stopping"
        self.peers["portal"].disconnect()
        self.peers["portal"].tick()
        self.drain()
        self.assertEqual(server.state, "stopping")
        self.assertEqual(server.pair, pair)
        self.assertEqual(len(self.applied), 1)
        self.assertNotEqual(self.peers["portal"].state, "ready")
