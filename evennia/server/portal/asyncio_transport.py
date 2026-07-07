"""Adapt an asyncio transport to the small Twisted-transport surface the Portal
session protocols read (T3).

The telnet + websocket session classes were written against a Twisted transport
(``write``/``loseConnection``/``getPeer``/``client``/``setTcpKeepAlive``). When
those same classes run on a native asyncio loop (``loop.create_server``), this
shim presents the asyncio transport with that surface, so the session code is
reused verbatim.
"""

import socket as _socket
from collections import namedtuple

from django.conf import settings

_Peer = namedtuple("_Peer", "host port")


def get_asyncio_loop():
    """The asyncio loop the Twisted reactor drives, or None (default reactor)."""
    from twisted.internet import reactor

    return getattr(reactor, "_asyncioEventloop", None)


def asyncio_servers_enabled():
    """Whether native-asyncio Portal servers/clients should be used (T3 gate)."""
    return bool(getattr(settings, "PORTAL_ASYNCIO_SERVERS", False)) and get_asyncio_loop() is not None


class AsyncioTransportShim:
    """Wrap an ``asyncio.Transport`` in the Twisted-transport API telnet/ws use."""

    def __init__(self, asyncio_transport):
        self._t = asyncio_transport
        self.disconnecting = False

    # -- outbound -------------------------------------------------------

    def write(self, data):
        if not self.disconnecting:
            self._t.write(data)

    def loseConnection(self):
        self.disconnecting = True
        try:
            self._t.close()
        except Exception:
            pass

    # -- peer info ------------------------------------------------------

    def getPeer(self):
        """Twisted-style peer address (has ``.host``/``.port``)."""
        peer = self._t.get_extra_info("peername")
        if peer:
            return _Peer(peer[0], peer[1] if len(peer) > 1 else 0)
        return _Peer(None, 0)

    @property
    def client(self):
        """``(host, port)`` tuple, as Twisted's TCP transport exposes."""
        peer = self._t.get_extra_info("peername")
        return peer if peer else None

    # -- socket options -------------------------------------------------

    def setTcpKeepAlive(self, enabled):
        sock = self._t.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1 if enabled else 0)
            except OSError:
                pass
