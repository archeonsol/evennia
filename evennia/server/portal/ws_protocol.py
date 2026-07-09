"""RFC 6455 WebSocket server on Twisted, driven by the sans-io ``wsproto``.

This replaces the autobahn dependency. ``wsproto`` is a pure state machine (no
I/O of its own): we feed it bytes off a Twisted ``Protocol`` and write the bytes
it hands back. It handles the HTTP upgrade handshake, framing, masking,
fragmentation, ping/pong and permessage-deflate for us, so nothing here is
hand-rolled framing.

Why this shape (vs. autobahn, vs. folding the socket into an ASGI server):
    - The webclient socket must stay on the **Portal** process so it survives a
      Server ``@reload`` (the Portal keeps its sockets; the resume stash covers
      the rare unclean blip). That rules out moving it onto the Server's ASGI
      app.
    - A plain Twisted ``Protocol`` runs natively on the reactor: no worker
      thread, no cross-thread session marshalling. Under the asyncio reactor it
      is already on the shared loop, so it gets the "one loop" win for free while
      still working on the default reactor (so it is testable in dev).
    - ``wsproto`` is sans-io, so when Twisted is eventually dropped the framing +
      session glue stays and only the thin transport shim below changes.

``WSProtocolBase`` exposes the small surface the Evennia webclient session layer
was written against (the autobahn call names are kept on purpose to minimise
churn): the ``onConnect``/``onOpen``/``onMessage``/``onClose`` callbacks, the
``sendMessage``/``sendClose`` senders, and the ``http_request_uri``/
``http_headers`` handshake attributes.
"""

import asyncio
from urllib.parse import urlparse

from twisted.internet import protocol
from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    AcceptConnection,
    BytesMessage,
    CloseConnection,
    Message,
    Ping,
    Pong,
    RejectConnection,
    Request,
    TextMessage,
)
from wsproto.extensions import PerMessageDeflate
from wsproto.utilities import RemoteProtocolError

from evennia.utils import logger

# RFC 6455 close codes (were sourced from autobahn's WebSocketServerProtocol).
CLOSE_NORMAL = 1000  # normal closure (JS close())
GOING_AWAY = 1001  # browser navigating away / tab closed


class Disconnected(Exception):
    """Raised when sending on an already-closed socket (autobahn-compatible)."""


class _ConnectionRequest:
    """Minimal stand-in for autobahn's ConnectionRequest passed to onConnect.

    Only the attributes the session layer reads are populated.
    """

    def __init__(self, protocols, headers, path, host):
        self.protocols = protocols
        self.headers = headers
        self.path = path
        self.host = host


class _WSCore:
    """Transport-agnostic sans-io websocket engine (no Twisted/asyncio import).

    Holds the byte pump (``feed`` -> wsproto -> events), message reassembly,
    ping/pong + close handling, and the autobahn-compatible ``sendMessage``/
    ``sendClose`` senders. It never touches a concrete transport: it calls the
    ``_transport_write``/``_transport_close`` hooks, which a *transport adapter*
    (Twisted or asyncio) supplies further down the MRO. A *role mixin* supplies
    ``_handle_event`` (server accepts / client initiates the handshake).

    This split is what lets the same protocol logic run under either the Twisted
    reactor or a native asyncio loop during the T3 (drop-Twisted) migration.
    """

    # autobahn-compatible close-code attributes (read by some session layers).
    CLOSE_STATUS_CODE_NORMAL = CLOSE_NORMAL
    CLOSE_STATUS_CODE_GOING_AWAY = GOING_AWAY

    def __init__(self, conn_type, *args, **kwargs):
        self._ws = WSConnection(conn_type)
        self._ws_open = False  # handshake completed
        self._ws_closed = False  # close frame sent/received
        self._closed_notified = False  # onClose fired exactly once
        self._msg_parts = []  # fragments of the message being received
        self._msg_is_binary = False
        # Continue cooperative init into the session mixin.
        super().__init__(*args, **kwargs)

    # -- neutral connection events (called by the transport adapter) ----

    def _on_connected(self):
        """Called once the transport is established. Client role overrides."""

    def feed(self, data):
        """Feed received bytes into the state machine and drain events."""
        try:
            self._ws.receive_data(data)
        except RemoteProtocolError as err:
            # Malformed handshake or frame: reject/close and drop the link.
            hint = getattr(err, "event_hint", None)
            if hint is not None:
                self._safe_write(self._ws.send(hint))
            self._lose_connection()
            return
        self._drain_events()

    def _on_conn_lost(self, reason=None):
        # If the socket dropped without a WS close handshake, it is unclean.
        self._notify_close(False, None, reason)

    # -- wsproto event pump ---------------------------------------------

    def _drain_events(self):
        try:
            events = list(self._ws.events())
        except RemoteProtocolError:
            self._lose_connection()
            return
        for event in events:
            self._handle_event(event)

    def _handle_event(self, event):
        raise NotImplementedError  # supplied by a role mixin

    def _handle_data_event(self, event):
        """Handle the events common to both roles once the socket is open.

        Returns True if the event was handled here.
        """
        if isinstance(event, (TextMessage, BytesMessage)):
            self._collect_message(event)
        elif isinstance(event, Ping):
            self._safe_write(self._ws.send(event.response()))
        elif isinstance(event, Pong):
            pass
        elif isinstance(event, CloseConnection):
            self._ws_closed = True
            # Echo the close per RFC 6455, then tear down.
            self._safe_write(self._ws.send(event.response()))
            self._notify_close(True, event.code, event.reason)
            self._lose_connection()
        else:
            return False
        return True

    def _collect_message(self, event):
        # A message may arrive as several frames; the first frame fixes the type.
        if not self._msg_parts:
            self._msg_is_binary = isinstance(event, BytesMessage)
        self._msg_parts.append(event.data)
        if not event.message_finished:
            return
        if self._msg_is_binary:
            payload = b"".join(self._msg_parts)
        else:
            # Match autobahn: onMessage always receives bytes + an isBinary flag.
            payload = "".join(self._msg_parts).encode("utf-8")
        self._msg_parts = []
        try:
            self.onMessage(payload, self._msg_is_binary)
        except Exception:
            logger.log_trace("websocket onMessage failed")

    # -- outbound (autobahn-compatible senders) -------------------------

    def sendMessage(self, payload, isBinary=False):
        """Send one WebSocket message. ``payload`` is bytes (autobahn contract)."""
        if self._ws_closed or not self._ws_open:
            raise Disconnected()
        try:
            if isBinary:
                data = bytes(payload)
            elif isinstance(payload, (bytes, bytearray)):
                data = payload.decode("utf-8")
            else:
                data = payload
            out = self._ws.send(Message(data=data))
        except Exception:
            # closed mid-send, or unencodable payload: treat as a dead link.
            raise Disconnected()
        try:
            self._transport_write(out)
        except Exception:
            raise Disconnected()

    def sendClose(self, code=CLOSE_NORMAL, reason=None):
        """Send a WebSocket close handshake and drop the link."""
        if self._ws_closed:
            self._lose_connection()
            return
        self._ws_closed = True
        try:
            self._safe_write(
                self._ws.send(CloseConnection(code=code or CLOSE_NORMAL, reason=reason))
            )
        except Exception:
            pass
        self._lose_connection()

    # -- helpers --------------------------------------------------------

    def _safe_write(self, data):
        if not data:
            return
        try:
            self._transport_write(data)
        except Exception:
            pass

    def _lose_connection(self):
        try:
            self._transport_close()
        except Exception:
            pass

    def _notify_close(self, was_clean, code, reason):
        if self._closed_notified:
            return
        self._closed_notified = True
        try:
            self.onClose(was_clean, code, reason)
        except Exception:
            logger.log_trace("websocket onClose failed")

    @staticmethod
    def _reason_str(reason):
        getter = getattr(reason, "getErrorMessage", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        return str(reason) if reason is not None else None


# -- role mixins (which end of the handshake) ---------------------------


class _WSServerRole:
    """Server role: accept the upgrade handshake."""

    def __init__(self, *args, **kwargs):
        # Handshake data the session layer reads (autobahn attribute names).
        self.http_request_uri = None
        self.http_headers = {}
        super().__init__(ConnectionType.SERVER, *args, **kwargs)

    def _handle_event(self, event):
        if isinstance(event, Request):
            self._accept_handshake(event)
        else:
            self._handle_data_event(event)

    def _accept_handshake(self, event):
        headers = {}
        for name, value in event.extra_headers:
            headers[name.decode("latin1").lower()] = value.decode("latin1")
        self.http_headers = headers
        self.http_request_uri = event.target  # path + query, e.g. "/?csessid&.."

        request = _ConnectionRequest(
            protocols=list(event.subprotocols or []),
            headers=headers,
            path=event.target,
            host=event.host,
        )
        subprotocol = None
        try:
            subprotocol = self.onConnect(request)
        except Exception:
            logger.log_trace("websocket onConnect failed")

        # Offer permessage-deflate unconditionally; wsproto negotiates it only
        # if the client actually offered it (a free win on big payloads).
        self._safe_write(
            self._ws.send(
                AcceptConnection(subprotocol=subprotocol, extensions=[PerMessageDeflate()])
            )
        )
        self._ws_open = True
        try:
            self.onOpen()
        except Exception:
            logger.log_trace("websocket onOpen failed")


class _WSClientRole:
    """Client role: initiate the upgrade handshake once connected."""

    def __init__(self, *args, **kwargs):
        super().__init__(ConnectionType.CLIENT, *args, **kwargs)

    def _on_connected(self):
        f = self.factory
        request = Request(
            host=getattr(f, "ws_host", "") or "",
            target=getattr(f, "ws_target", "/") or "/",
            subprotocols=list(getattr(f, "ws_subprotocols", []) or []),
            extensions=[],
            extra_headers=list(getattr(f, "ws_headers", []) or []),
        )
        self._safe_write(self._ws.send(request))

    def _handle_event(self, event):
        if isinstance(event, AcceptConnection):
            self._ws_open = True
            try:
                self.onOpen()
            except Exception:
                logger.log_trace("websocket client onOpen failed")
        elif isinstance(event, RejectConnection):
            self._notify_close(False, getattr(event, "status_code", None), None)
            self._lose_connection()
        else:
            self._handle_data_event(event)


# -- transport adapters (which event loop) ------------------------------


class _TwistedWSAdapter(protocol.Protocol):
    """Drives ``_WSCore`` from a Twisted transport."""

    def connectionMade(self):
        self._on_connected()

    def dataReceived(self, data):
        self.feed(data)

    def connectionLost(self, reason):
        self._on_conn_lost(self._reason_str(reason))

    def _transport_write(self, data):
        self.transport.write(data)

    def _transport_close(self):
        self.transport.loseConnection()

    def _peer_host(self):
        peer = self.transport.getPeer()
        return getattr(peer, "host", None)


class _AsyncioWSAdapter(asyncio.Protocol):
    """Drives ``_WSCore`` from a native asyncio transport (T3).

    Same sans-io core, native event loop: no Twisted reactor involved. Lets the
    websocket run on an ``loop.create_server`` endpoint alongside (or instead of)
    the Twisted services during the drop-Twisted migration.
    """

    def connection_made(self, transport):
        self.transport = transport
        self._on_connected()

    def data_received(self, data):
        self.feed(data)

    def connection_lost(self, exc):
        self._on_conn_lost(str(exc) if exc else None)

    def _transport_write(self, data):
        self.transport.write(data)

    def _transport_close(self):
        self.transport.close()

    def _peer_host(self):
        peer = self.transport.get_extra_info("peername")
        return peer[0] if peer else None


# -- concrete protocols (role x transport) ------------------------------


class WSProtocolBase(_WSServerRole, _WSCore, _TwistedWSAdapter):
    """Server-side WebSocket over Twisted (the webclient uses this).

    Subclasses implement the ``on*`` callbacks (the session layer does). The
    transport surface (``sendMessage``/``sendClose``) mirrors autobahn so the
    existing session code is reused verbatim.
    """


class WSClientProtocolBase(_WSClientRole, _WSCore, _TwistedWSAdapter):
    """Client-side WebSocket over Twisted (Discord-gateway and grapevine bots).

    Mirrors autobahn's ``WebSocketClientProtocol`` surface. The target host,
    path, subprotocols and extra HTTP headers are read off the factory (set by
    ``connect_ws``).
    """


class WSServerFactory(protocol.ServerFactory):
    """Plain Twisted factory for the websocket (replaces autobahn's).

    ``protocol`` and ``sessionhandler`` are assigned by the portal service.
    permessage-deflate is handled in the protocol, so there is nothing to
    configure here.
    """

    noisy = False
    sessionhandler = None


def encode_ws_headers(headers):
    """Normalise a header mapping to wsproto's ``[(bytes, bytes)]`` form.

    Accepts autobahn/Twisted-style ``{name: [values]}`` or plain ``{name:
    value}``; used to build the outbound-client handshake headers (auth tokens,
    user-agent) for the Discord/grapevine bots.
    """
    out = []
    for name, value in (headers or {}).items():
        name_b = name.encode("latin1") if isinstance(name, str) else name
        values = value if isinstance(value, (list, tuple)) else [value]
        for val in values:
            val_b = val.encode("latin1") if isinstance(val, str) else val
            out.append((name_b, val_b))
    return out


class _AsyncioWSClientProtocol(asyncio.Protocol):
    """Drive a ``WSClientProtocolBase`` session over a native asyncio transport.

    Composition mirror of the inbound asyncio adapters: owns the client session
    (built by ``factory.buildProtocol``), hands it an ``AsyncioTransportShim``,
    and forwards the loop's callbacks. ``closed`` resolves on disconnect so the
    reconnect driver can wait on it.
    """

    def __init__(self, factory, loop):
        from evennia.server.portal.asyncio_transport import AsyncioTransportShim

        self._shim_cls = AsyncioTransportShim
        self.session = factory.buildProtocol(None)
        self.closed = loop.create_future()

    def connection_made(self, transport):
        self.session.transport = self._shim_cls(transport)
        # client role: connectionMade -> _on_connected -> send the upgrade request
        self.session.connectionMade()

    def data_received(self, data):
        self.session.dataReceived(data)

    def connection_lost(self, exc):
        try:
            self.session.connectionLost(str(exc) if exc else None)
        finally:
            if not self.closed.done():
                self.closed.set_result(True)


async def connect_ws_asyncio(factory):
    """Connect an outbound WS client factory over asyncio, with reconnect.

    The asyncio counterpart of ``connect_ws`` + Twisted's ReconnectingClientFactory:
    parses ``factory.ws_url``, connects (TLS-verified for wss), drives the wsproto
    client session, and on disconnect reconnects with capped exponential backoff
    until ``factory.stopping`` is set. Reads the same ``ws_url``/backoff attrs the
    Twisted factory used (``initialDelay``/``factor``/``maxDelay``).
    """
    import asyncio as _asyncio
    import ssl as _ssl

    def _stopping():
        if getattr(factory, "stopping", False):
            return True
        bot = getattr(factory, "bot", None)
        return bool(bot is not None and getattr(bot, "stopping", False))

    loop = _asyncio.get_event_loop()
    delay = getattr(factory, "initialDelay", 1)
    factor = getattr(factory, "factor", 1.5)
    max_delay = getattr(factory, "maxDelay", 60)

    while not _stopping():
        parsed = urlparse(factory.ws_url)
        secure = parsed.scheme in ("wss", "https")
        host = parsed.hostname
        port = parsed.port or (443 if secure else 80)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        factory.ws_host = host
        factory.ws_target = target

        try:
            ssl_ctx = _ssl.create_default_context() if secure else None
            _, proto = await loop.create_connection(
                lambda: _AsyncioWSClientProtocol(factory, loop),
                host,
                port,
                ssl=ssl_ctx,
                server_hostname=host if secure else None,
            )
            delay = getattr(factory, "initialDelay", 1)  # reset backoff on success
            closed_wait = _asyncio.ensure_future(proto.closed)
            while not closed_wait.done():
                if _stopping():
                    try:
                        proto.session.transport.loseConnection()
                    except (AttributeError, OSError):
                        pass
                try:
                    await _asyncio.wait_for(_asyncio.shield(closed_wait), timeout=0.25)
                    break
                except _asyncio.TimeoutError:
                    continue
        except (OSError, _asyncio.TimeoutError):
            # Transient connection failure (DNS, refused, reset, timeout): retry.
            logger.log_trace("asyncio ws client connection failed; will retry")
        except Exception:
            # Non-transient (misconfig, programming error): stop, do not spin.
            logger.log_trace("asyncio ws client hit a non-transient error; stopping reconnect")
            break

        if _stopping():
            break
        await _asyncio.sleep(delay)
        delay = min(max_delay, delay * factor)


def connect_ws(factory, reactor=None):
    """Connect an outbound websocket client factory (replaces ``connectWS``).

    When ``PORTAL_ASYNCIO_SERVERS`` is on and the asyncio reactor provides a
    shared loop, starts ``connect_ws_asyncio`` on that loop (no Twisted
    ``connectTCP``/``connectSSL``). Dev without the flag still uses Twisted.
    """
    from django.conf import settings

    from evennia.server.portal.asyncio_transport import asyncio_servers_enabled, get_asyncio_loop

    if asyncio_servers_enabled():
        get_asyncio_loop().create_task(connect_ws_asyncio(factory))
        return None

    if getattr(settings, "PORTAL_ASYNCIO_SERVERS", False):
        logger.log_err(
            "PORTAL_ASYNCIO_SERVERS=True requires an asyncio bootstrap loop "
            "(no shared loop in this process). Outbound WS client "
            "will not connect."
        )
        return None

    if reactor is None:
        from twisted.internet import reactor

    parsed = urlparse(factory.ws_url)
    secure = parsed.scheme in ("wss", "https")
    host = parsed.hostname
    port = parsed.port or (443 if secure else 80)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"

    factory.ws_host = host
    factory.ws_target = target

    if secure:
        from twisted.internet import ssl

        return reactor.connectSSL(host, port, factory, ssl.optionsForClientTLS(host))
    return reactor.connectTCP(host, port, factory)
