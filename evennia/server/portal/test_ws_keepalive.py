"""
The WebSocket core's keepalive surface, over a real wsproto handshake: a ping
the peer's stack answers by itself, the inbound timestamp that answer refreshes,
and the hard abort used on a peer that stopped reading.
"""

from unittest import TestCase

from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, Ping, Request

from evennia.server.portal.ws_protocol import WSProtocolBase


class _Wire:
    """Collects what the server writes; records how it was closed."""

    def __init__(self):
        self.out = bytearray()
        self.closed = False
        self.aborted = False

    def write(self, data):
        self.out += data

    def loseConnection(self):
        self.closed = True

    def abortConnection(self):
        self.aborted = True

    def take(self):
        data = bytes(self.out)
        self.out.clear()
        return data


class _Server(WSProtocolBase):
    """A server socket with no session behind it."""

    def onConnect(self, request):
        return None

    def onOpen(self):
        pass

    def onMessage(self, payload, isBinary):
        pass

    def onClose(self, wasClean, code=None, reason=None):
        pass


class TestKeepaliveSurface(TestCase):
    def setUp(self):
        self.server = _Server()
        self.server.transport = self.wire = _Wire()
        self.client = WSConnection(ConnectionType.CLIENT)
        self.server.feed(self.client.send(Request(host="example.test", target="/")))
        events = self._client_events()
        self.assertTrue(any(isinstance(e, AcceptConnection) for e in events))

    def _client_events(self):
        self.client.receive_data(self.wire.take())
        return list(self.client.events())

    def test_a_ping_is_answered_by_the_peer_stack(self):
        self.assertTrue(self.server.sendPing(b"k"))
        pings = [e for e in self._client_events() if isinstance(e, Ping)]
        self.assertEqual([p.payload for p in pings], [b"k"])
        # The browser's side: a pong goes back without any page code running.
        self.server.last_received = 0.0
        self.server.feed(self.client.send(pings[0].response()))
        self.assertGreater(self.server.last_received, 0.0)

    def test_no_ping_once_the_socket_is_closing(self):
        self.server.sendClose()
        self.assertFalse(self.server.sendPing())

    def test_abort_drops_the_link_without_waiting_to_flush(self):
        self.server.abortConnection()
        self.assertTrue(self.wire.aborted)
        self.assertFalse(self.server.sendPing())

    def test_abort_falls_back_to_a_close_on_transports_without_it(self):
        class _PlainWire(_Wire):
            abortConnection = None

        wire = self.server.transport = _PlainWire()
        self.server.abortConnection()
        self.assertTrue(wire.closed)
