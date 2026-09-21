"""Deterministic peer ordering and session snapshot confirmation tests."""

from unittest import TestCase

from django.test import override_settings

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
            self.peers[role] = self._peer(role)

    def _peer(self, role):
        """Build one peer wired to the shared fake clock and wire."""
        return BusHandshake(
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
        """A delayed snapshot has its full staleness deadline to complete."""
        self.peers["portal"].tick()
        role, probe = self.wire.pop(0)
        self.peers["server"].receive(probe)
        role, offer = self.wire.pop(0)
        self.peers["portal"].receive(offer)
        pending = list(self.wire)
        self.now += 2
        self.peers["portal"].tick()
        self.assertEqual(self.wire, pending)
        self.drain()
        self.assertEqual(self.peers["portal"].state, "ready")

    def test_repeated_probes_replace_one_offer(self):
        """A re-probing portal may not fill the offer table slot by slot."""

        server = self.peers["server"]
        for _ in range(9):
            self.now += 1
            server.receive({"kind": "probe", "portal": ["portal", "a"], "probe": "x1"})
        self.wire.clear()
        server.receive({"kind": "probe", "portal": ["portal", "b"], "probe": "x2"})
        kinds = [frame["kind"] for _role, frame in self.wire]
        self.assertIn("offer", kinds)

    def test_offer_table_at_capacity_answers_the_probe(self):
        """At capacity the oldest bound offer yields to a fresh probe."""

        server = self.peers["server"]
        self.now += 1
        for index in range(8):
            server.receive({"kind": "probe", "portal": ["portal", f"p{index}"], "probe": "p"})
        self.wire.clear()
        server.receive({"kind": "probe", "portal": ["portal", "p8"], "probe": "p"})
        kinds = [frame["kind"] for _role, frame in self.wire]
        self.assertIn("offer", kinds)
        self.assertEqual(len(server._offers), 8)
        pairs = [tuple(offer["pair"][0]) for offer in server._offers.values()]
        self.assertNotIn(("portal", "p0"), pairs)
        self.assertIn(("portal", "p8"), pairs)

    def test_unanswered_probe_is_reissued(self):
        """A probe lost before the peer subscribed is retried on cadence."""
        self.now += 1
        self.peers["portal"].tick()
        first = self.wire.pop(0)[1]
        self.now += 1
        self.peers["portal"].tick()
        second = self.wire.pop(0)[1]
        self.assertEqual(second["kind"], "probe")
        self.assertNotEqual(second["probe"], first["probe"])

    def test_peer_lease_expires(self):
        """A dead peer closes action admission without PID assumptions."""
        self.connect()
        self.now += max(peer.timeout for peer in self.peers.values()) + 1
        for peer in self.peers.values():
            peer.tick()
            self.assertNotEqual(peer.state, "ready")

    @override_settings(BUS_HANDSHAKE_TIMEOUT=30)
    def test_timeout_honors_setting(self):
        """Deployments can widen the lease for long synchronous turns."""
        peer = BusHandshake(
            "portal",
            send=lambda frame: None,
            snapshot=lambda: (0, {}),
            apply=lambda payload: None,
            state_snapshot=lambda: {},
            apply_state=lambda payload: None,
            on_ready=lambda: None,
            on_unavailable=lambda: None,
        )
        self.assertEqual(peer.timeout, 30.0)

    @override_settings(BUS_HANDSHAKE_TIMEOUT=0)
    def test_timeout_floor_keeps_the_lease_above_the_heartbeat(self):
        """A zero lease would disconnect a healthy peer on the first tick."""
        peer = BusHandshake(
            "portal",
            send=lambda frame: None,
            snapshot=lambda: (0, {}),
            apply=lambda payload: None,
            state_snapshot=lambda: {},
            apply_state=lambda payload: None,
            on_ready=lambda: None,
            on_unavailable=lambda: None,
        )
        self.assertEqual(peer.timeout, BusHandshake.heartbeat)

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


class TestExchangeStaleness(TestCase):
    """The staleness bound, not the liveness lease, gates replay defenses."""

    def setUp(self):
        self.now = 0
        self.wire = []
        self.applied = []
        self.ready = []
        self.peers = {}
        for role in ("portal", "server"):
            peer = self._peer(role)
            peer.timeout = 12.0
            peer.staleness = 4.0
            self.peers[role] = peer

    def drain(self):
        """Deliver all emitted frames in publication order."""
        for _ in range(30):
            if not self.wire:
                return
            role, frame = self.wire.pop(0)
            self.peers["server" if role == "portal" else "portal"].receive(frame)
        self.fail("handshake did not converge")

    def _bind_offer(self, at):
        """Bind a server-side offer for a portal identity, dated ``at``."""
        server = self.peers["server"]
        portal = ["portal", "a"]
        offer = {
            "probe": "p1",
            "challenge": "c1",
            "pair": [portal, server.identity],
            "at": at,
        }
        server._offers["c1"] = offer
        server._offer_slots[tuple(portal)] = "c1"
        return offer

    def test_staleness_defaults_below_the_lease(self):
        """The replay bound keeps the pre-split four-second value."""
        peer = self._peer("portal")
        self.assertEqual(peer.timeout, 12.0)
        self.assertEqual(peer.staleness, 4.0)

    @override_settings(BUS_HANDSHAKE_STALENESS=6.0)
    def test_staleness_honors_setting(self):
        """Deployments can widen the replay bound independently of the lease."""
        peer = self._peer("portal")
        self.assertEqual(peer.staleness, 6.0)
        self.assertEqual(peer.timeout, 12.0)

    @override_settings(BUS_HANDSHAKE_TIMEOUT=2.0)
    def test_staleness_is_ceiled_by_the_lease(self):
        """A bound above the handshake budget would accept then kill exchanges."""
        peer = self._peer("portal")
        self.assertEqual(peer.staleness, 2.0)

    @override_settings(BUS_HANDSHAKE_STALENESS=0)
    def test_staleness_floor_keeps_the_bound_above_the_heartbeat(self):
        """A zero bound would reject every re-issued offer."""
        peer = self._peer("portal")
        self.assertEqual(peer.staleness, BusHandshake.heartbeat)

    def test_old_snapshot_cannot_rewrite_sessions(self):
        """A snapshot older than the bound carries no state authority."""
        self._bind_offer(self.now)
        self.now += 6
        self.peers["server"].receive(
            {
                "kind": "snapshot",
                "probe": "p1",
                "challenge": "c1",
                "pair": [["portal", "a"], self.peers["server"].identity],
                "payload": {"sessions": {}},
            }
        )
        self.assertEqual(self.applied, [])
        self.assertEqual(self.peers["server"].state, "disconnected")

    def test_delayed_pulse_keeps_liveness_credit(self):
        """A heartbeat answer older than the bound still refreshes the lease."""
        self._bind_offer(self.now)
        self.now += 6
        server = self.peers["server"]
        server.state = "ready"
        server.pair = [["portal", "a"], server.identity]
        server._last_seen = self.now - 6
        self.peers["server"].receive(
            {
                "kind": "pulse",
                "probe": "p1",
                "challenge": "c1",
                "pair": [["portal", "a"], server.identity],
            }
        )
        self.assertEqual(server._last_seen, self.now)
        self.assertEqual(self.wire[-1][1]["kind"], "pulse_ack")

    def test_late_confirm_is_refused_at_the_bound(self):
        """Confirmation beyond the bound cannot complete the exchange."""
        offer = self._bind_offer(self.now)
        self.peers["server"].state = "synchronizing"
        self.peers["server"]._exchange = offer
        self.now += 6
        self.peers["server"].receive(
            {
                "kind": "confirm",
                "probe": "p1",
                "challenge": "c1",
                "pair": offer["pair"],
            }
        )
        self.assertEqual(self.peers["server"].state, "synchronizing")
        self.assertEqual(self.ready, [])

    def test_stale_offer_answer_is_refused(self):
        """A portal rejects an offer answer past the bound, then re-discovers."""
        portal = self.peers["portal"]
        portal.tick()
        _role, probe = self.wire.pop(0)
        self.peers["server"].receive(probe)
        _role, offer = self.wire.pop(0)
        self.now += 6
        portal.receive(offer)
        self.assertNotEqual(portal.state, "ready")
        self.now += 1
        portal.tick()
        self.drain()
        self.assertEqual(portal.state, "ready")

    def _peer(self, role):
        """Build one peer wired to this suite's fake clock and wire."""
        return BusHandshake(
            role,
            send=lambda frame, role=role: self.wire.append((role, frame)),
            snapshot=lambda: (0, {"sessions": {}}),
            apply=lambda payload: self.applied.append(payload),
            state_snapshot=lambda: {"auth": "current"},
            apply_state=lambda payload: None,
            on_ready=lambda role=role: self.ready.append(role),
            on_unavailable=lambda: None,
            now=lambda: self.now,
        )
