"""Fresh peer discovery and confirmed session reconciliation for the Redis bus."""

import time
from uuid import uuid4


class BusHandshake:
    """Negotiate a pair without granting authority to unsolicited discovery.

    All methods run on the event loop. The wire sends only current state and
    random challenges; lifecycle requests never enter this state machine.
    """

    timeout = 4.0
    heartbeat = 1.0

    def __init__(
        self,
        role,
        *,
        send,
        snapshot,
        apply,
        state_snapshot,
        apply_state,
        on_ready,
        on_unavailable,
        now=time.monotonic,
    ):
        """Bind peer-specific state operations and a monotonic clock."""
        self.role = role
        self.process = uuid4().hex
        self.epoch = uuid4().hex
        self.state = "disconnected"
        self.pair = None
        self._send = send
        self._snapshot = snapshot
        self._apply = apply
        self._state_snapshot = state_snapshot
        self._apply_state = apply_state
        self._on_ready = on_ready
        self._on_unavailable = on_unavailable
        self._now = now
        self._last_seen = now()
        self._last_probe = -self.heartbeat
        self._pending = None
        self._offers = {}
        self._exchange = None

    @property
    def identity(self):
        """Return process and transport incarnation identities."""
        return [self.process, self.epoch]

    def disconnect(self):
        """Invalidate this transport incarnation and its pending confirmations."""
        if self.state == "stopping":
            return
        self.state = "disconnected"
        self.pair = None
        self.epoch = uuid4().hex
        self._pending = self._exchange = None
        self._offers.clear()
        self._last_probe = -self.heartbeat
        self._on_unavailable()

    def tick(self):
        """Expire peer leases and start bounded fresh discovery when needed."""
        if self.state == "stopping":
            return
        now = self._now()
        if self.state == "ready" and now - self._last_seen >= self.timeout:
            self.disconnect()
        if self._pending and now - self._pending["at"] >= self.timeout:
            self.disconnect()
        if self._exchange and now - self._exchange["at"] >= self.timeout:
            self.disconnect()
        if self.role != "portal" or self._pending:
            return
        if now - self._last_probe < self.heartbeat:
            return
        self._last_probe = now
        self._pending = {"probe": uuid4().hex, "at": now}
        self._send({"kind": "probe", "portal": self.identity, "probe": self._pending["probe"]})

    def _matches(self, frame, exchange):
        """Check both reciprocal challenges and the exact negotiated pair."""
        return exchange is not None and all(
            frame.get(key) == exchange.get(key) for key in ("probe", "challenge", "pair")
        )

    def _emit(self, kind, exchange, **payload):
        """Send one stage with its correlation identity."""
        self._send(
            {
                "kind": kind,
                **{key: exchange[key] for key in ("probe", "challenge", "pair")},
                **payload,
            }
        )

    def receive(self, frame):
        """Apply a stage only after its freshly issued challenge is answered."""
        if self.state == "stopping" or not isinstance(frame, dict):
            return
        kind = frame.get("kind")
        if self.role == "server":
            self._receive_server(kind, frame)
        else:
            self._receive_portal(kind, frame)

    def _receive_server(self, kind, frame):
        """Answer discovery without changing an established generation."""
        if kind == "probe":
            portal = frame.get("portal")
            if not isinstance(portal, list) or len(portal) != 2:
                return
            offer = {
                "probe": frame.get("probe"),
                "challenge": uuid4().hex,
                "pair": [portal, self.identity],
                "at": self._now(),
            }
            self._offers = {
                key: value
                for key, value in self._offers.items()
                if self._now() - value["at"] < self.timeout
            }
            if len(self._offers) >= 8:
                return
            self._offers[offer["challenge"]] = offer
            self._emit("offer", offer, ready=self.state == "ready" and self.pair == offer["pair"])
        elif kind in ("pulse", "snapshot"):
            offer = self._offers.get(frame.get("challenge"))
            if not self._matches(frame, offer):
                return
            if self._now() - offer["at"] >= self.timeout:
                return
            del self._offers[frame["challenge"]]
            if kind == "pulse":
                if self.state == "ready" and self.pair == frame["pair"]:
                    self._last_seen = self._now()
                    self._emit("pulse_ack", frame)
                return
            self.state = "synchronizing"
            self._exchange = offer
            self.pair = frame["pair"]
            self._apply(frame["payload"])
            self._emit("applied", frame)
        elif kind == "confirm" and self._matches(frame, self._exchange):
            if self._now() - self._exchange["at"] >= self.timeout:
                return
            self._exchange = None
            self.state = "ready"
            self._last_seen = self._now()
            self._emit("ready", frame, payload=self._state_snapshot())
            self._on_ready()

    def _receive_portal(self, kind, frame):
        """Confirm application against the current local socket revision."""
        pending = self._pending
        if not pending or frame.get("probe") != pending["probe"]:
            return
        if self._now() - pending["at"] >= self.timeout:
            return
        if kind == "offer":
            pair = frame.get("pair")
            if not isinstance(pair, list) or len(pair) != 2 or pair[0] != self.identity:
                return
            pending.update(challenge=frame.get("challenge"), pair=pair)
            if self.state == "ready" and self.pair == pair and frame.get("ready"):
                self._emit("pulse", pending)
            else:
                self.state = "synchronizing"
                self.pair = pair
                revision, payload = self._snapshot()
                pending["revision"] = revision
                self._emit("snapshot", pending, payload=payload)
        elif not self._matches(frame, pending):
            return
        elif kind == "pulse_ack" and self.state == "ready":
            self._last_seen = self._now()
            self._pending = None
        elif kind in ("applied", "ready"):
            if self._snapshot()[0] != pending.get("revision"):
                self.disconnect()
                return
            if kind == "applied":
                self._emit("confirm", pending)
            else:
                self._apply_state(frame["payload"])
                self.state = "ready"
                self._last_seen = self._now()
                self._pending = None
                self._on_ready()
