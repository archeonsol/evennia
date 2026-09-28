"""
Webclient based on websockets with MUD Standards subprotocol support.

This implements a webclient with WebSockets (http://en.wikipedia.org/wiki/WebSocket)
on top of the sans-io ``wsproto`` state machine driven from a Twisted protocol
(see ``evennia/server/portal/ws_protocol.py``; this replaced autobahn-python).
It is used together with evennia/web/media/javascript/evennia_websocket_webclient.js.

Subprotocol Negotiation (RFC 6455 Sec-WebSocket-Protocol):
    When a client connects, it may offer one or more WebSocket subprotocols
    via the Sec-WebSocket-Protocol header. This module negotiates the best
    match from the server's supported list (configured via
    settings.WEBSOCKET_SUBPROTOCOLS) and selects the appropriate wire format
    codec for the connection's lifetime.

    Supported subprotocols (per https://mudstandards.org/websocket/):
        - v1.evennia.com: Evennia's legacy JSON array format
        - json.mudstandards.org: MUD Standards JSON envelope format
        - gmcp.mudstandards.org: GMCP over WebSocket
        - terminal.mudstandards.org: Raw ANSI terminal over WebSocket

    If no subprotocol is negotiated (legacy client with no header),
    the v1.evennia.com format is used as the default.

All data coming into the webclient via the v1.evennia.com format is in the
form of valid JSON on the form

`["inputfunc_name", [args], {kwarg}]`

which represents an "inputfunc" to be called on the Evennia side with *args, **kwargs.
The most common inputfunc is "text", which takes just the text input
from the command line and interprets it as an Evennia Command: `["text", ["look"], {}]`

"""

import asyncio
import json
import secrets
import time
import weakref
from collections import deque

from django.conf import settings

from evennia.server.portal.asyncio_transport import AsyncioTransportShim
from evennia.server.portal.ws_protocol import (
    CLOSE_NORMAL,
    GOING_AWAY,
    Disconnected,
    HandshakeDenied,
    WSProtocolBase,
)
from evennia.utils import clock, logger
from evennia.utils.utils import class_from_module, mod_import

_CLIENT_SESSIONS = mod_import(settings.SESSION_ENGINE).SessionStore

# --- Session continuity ---
# A socket and a game session are different things. A phone that changes
# network, sleeps, or loses its radio for a few seconds drops the socket, but
# the player has not left. So only an explicit end finishes the session: the
# browser closing with 1000/1001 (a closed tab, a navigation), the client's
# `websocket_close`, or the Server disconnecting it. Any other loss *holds* the
# session: the Portal keeps it under its sessid for WEBCLIENT_RESUME_GRACE
# seconds, still recording what the Server sends, and the Server is not told.
# A reconnect that proves it is the same tab of the same account takes the
# session over in place (same sessid, same Server session, no logout or login
# hooks) and is replayed what it missed. Only an unclaimed hold reaches the
# Server as a disconnect.
#
# The proof has two parts. The browser's signed-in account (the Django session
# the handshake presents) must be the account the held session is logged in
# as, so no one can claim, or read the output of, another account's session.
# And the client must present the resume token the Portal issued to that
# connection in its last `hello` reply, because an account alone cannot tell
# two tabs apart. The token lives in the tab's sessionStorage and is replaced
# on every takeover, so a copied tab cannot bounce a session between windows.
#
# Every outbound JSON frame is stamped with a monotonic ``s`` (seq) and kept in
# a bounded per-session buffer; the client presents the last seq it applied and
# is sent what came after.
RESUME_BUFFER_MAX = 400  # frames kept per connection
#: Default seconds a dropped session is held (``settings.WEBCLIENT_RESUME_GRACE``).
RESUME_GRACE_SECONDS = 90
#: Hard cap on sessions held at once. Each keeps a Server session and up to
#: RESUME_BUFFER_MAX frames alive, so the oldest hold ends first past this.
RESUME_HOLD_MAX = 512
#: How long an Azaban socket may stay silent before its session starts without
#: a resume. The shell sends `hello` as soon as the socket opens.
HELLO_WAIT_SECONDS = 5
#: Default seconds between protocol pings (``settings.WEBCLIENT_PING_DELAY``).
PING_DELAY_SECONDS = 20
#: Default seconds of silence after which a peer is taken to be gone
#: (``settings.WEBCLIENT_PING_TIMEOUT``). Never less than two ping intervals.
PING_TIMEOUT_SECONDS = 60
#: Close code sent to a socket whose session another connection took over.
CLOSE_SUPERSEDED = 4001
#: resume token -> the WebSocketClient that owns it, live or held.
_RESUMABLE = {}
#: Open sockets the keepalive pings.
_LIVE_SOCKETS = weakref.WeakSet()
#: The Portal-wide keepalive loop, started with the first socket.
_KEEPALIVE = None

#: Session state that belongs to the game session rather than to its socket, and
#: so moves across when a reconnect takes a held session over. Socket facts
#: (address, csessid, nonce, headers, fingerprints) stay the new socket's own.
_SESSION_STATE = (
    "sessid",
    "suid",
    "uid",
    "uname",
    "logged_in",
    "bid",
    "conn_time",
    "cmd_last",
    "cmd_last_visible",
    "cmd_total",
    "server_data",
    "cmdset_storage_string",
    "server_connected",
    "command_counter",
    "command_counter_reset",
    "_bus_notice_at",
    # The bus socket incarnation. Keeping it is what tells the Server that
    # the session it holds is still this one, not a new connection.
    "_bus_socket_id",
    "_bus_protocol_auth",
    "_bus_confirmed",
)

# --- Frame batching ---
# One outputfunc is one frame is one WebSocket send, so a single room look costs
# several sends and several client re-renders. A client that declares
# ``caps.batching`` gets a burst coalesced into one ``{"t": "batch", "frames":
# [...]}`` envelope flushed at the end of the current reactor iteration. Order is
# preserved, and the batch is stamped as a single seq for resume purposes.
#: Never hold more than this many frames before forcing a flush.
BATCH_MAX_FRAMES = 64

_DEFAULT_MAX_OUTGOING_BYTES = 32 * 1024 * 1024
_DEFAULT_BATCH_BYTES = 1024 * 1024
_DEFAULT_RESUME_BYTES = 32 * 1024 * 1024
_DEFAULT_RESUME_STASH_BYTES = 128 * 1024 * 1024


def _setting_bytes(name, default):
    """Read a positive byte limit from settings."""
    value = getattr(settings, name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"settings.{name} must be a positive integer")
    return value


def _frame_bytes(frame):
    """Return the serialized UTF-8 size of an outbound frame."""
    if isinstance(frame, dict):
        frame = json.dumps(frame)
    if isinstance(frame, str):
        frame = frame.encode("utf-8")
    return len(frame)


def _check_outgoing_size(frame):
    """Reject a frame beyond the configured atomic WebSocket limit."""
    size = _frame_bytes(frame)
    limit = _setting_bytes("WEBSOCKET_MAX_OUTGOING_BYTES", _DEFAULT_MAX_OUTGOING_BYTES)
    if size > limit:
        raise ValueError(f"outgoing WebSocket frame is {size} bytes; limit is {limit}")


def _trim_replay_frames(frames):
    """Evict oldest complete frames until count and byte limits both hold."""
    limit = _setting_bytes("WEBSOCKET_RESUME_BYTES", _DEFAULT_RESUME_BYTES)
    total = sum(len(frame) for _, frame in frames)
    while len(frames) > RESUME_BUFFER_MAX or total > limit:
        _, frame = frames.popleft()
        total -= len(frame)


def _setting_seconds(name, default):
    """Read a non-negative duration in seconds from settings."""
    value = getattr(settings, name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"settings.{name} must be a non-negative number of seconds")
    return value


def _resume_grace():
    """Seconds a dropped session is held for its tab to come back (0 disables)."""
    return _setting_seconds("WEBCLIENT_RESUME_GRACE", RESUME_GRACE_SECONDS)


def _keepalive_timing():
    """Return ``(ping_delay, silence_timeout)`` in seconds; a zero delay disables."""
    delay = _setting_seconds("WEBCLIENT_PING_DELAY", PING_DELAY_SECONDS)
    timeout = _setting_seconds("WEBCLIENT_PING_TIMEOUT", PING_TIMEOUT_SECONDS)
    # One late pong must never read as a dead peer.
    return delay, max(timeout, 2 * delay)


def _call_later(delay, fn, *args):
    """Schedule ``fn`` on the Portal loop, or return None where none is running."""
    if not clock.loop_running():
        return None
    return clock.call_later(delay, fn, *args, _task_kind="transport")


def _buffer_bytes(session):
    """Bytes of replay a session holds (frames are ASCII, so length is size)."""
    return sum(len(frame) for _, frame in getattr(session, "out_buffer", None) or ())


def _enforce_hold_limits():
    """End overdue holds, then the oldest ones until the count and byte caps hold.

    Holds end on their own timer; the overdue sweep here only matters if one
    never fired. Held sessions each keep a Server session alive, so an open-close
    loop must not be able to pile them up.
    """
    now = time.monotonic()
    held = []
    for owner in list(_RESUMABLE.values()):
        if owner._held_until is None:
            continue
        if owner._held_until <= now:
            owner._expire_hold("grace expired")
        else:
            held.append(owner)
    held.sort(key=lambda owner: owner._held_until)
    byte_limit = _setting_bytes("WEBSOCKET_RESUME_STASH_BYTES", _DEFAULT_RESUME_STASH_BYTES)
    total = sum(_buffer_bytes(owner) for owner in held)
    while held and (len(held) > RESUME_HOLD_MAX or total > byte_limit):
        oldest = held.pop(0)
        total -= _buffer_bytes(oldest)
        oldest._expire_hold("hold capacity reached")


def _ensure_keepalive():
    """Start the Portal-wide keepalive loop once, if pings are enabled."""
    global _KEEPALIVE
    if _KEEPALIVE is not None and _KEEPALIVE.running:
        return
    delay, _timeout = _keepalive_timing()
    if not delay or not clock.loop_running():
        return
    _KEEPALIVE = clock.looping(delay, _keepalive_sweep)


def _keepalive_sweep():
    """Ping every open socket, and drop the ones that stopped answering.

    The ping keeps the path warm: carrier NATs and proxies forget a silent TCP
    flow, and a browser idling in a room sends nothing. The silence check is what
    finds a phone that vanished without a FIN, which otherwise sits "connected"
    until the kernel gives up retransmitting, minutes later.
    """
    _delay, timeout = _keepalive_timing()
    now = time.monotonic()
    for sock in list(_LIVE_SOCKETS):
        try:
            sock._keepalive_tick(now, timeout)
        except Exception:
            logger.log_trace("webclient keepalive")
    _enforce_hold_limits()


# CLOSE_NORMAL (1000) / GOING_AWAY (1001) are imported from ws_protocol.

_BASE_SESSION_CLASS = class_from_module(settings.BASE_SESSION_CLASS)

# --- Wire format support ---
# Import wire formats lazily to avoid circular imports at module level.
# The WIRE_FORMATS dict and format instances are created on first use.
_wire_formats = None


def _get_wire_formats():
    """
    Lazily load and return the wire format registry.

    Returns:
        dict: Mapping of subprotocol name -> WireFormat instance.

    """
    global _wire_formats
    if _wire_formats is None:
        try:
            from evennia.server.portal.wire_formats import WIRE_FORMATS

            _wire_formats = WIRE_FORMATS
        except Exception:
            from evennia.utils import logger

            logger.log_trace("Failed to load wire format registry")
            _wire_formats = {}
    return _wire_formats


def _get_supported_subprotocols():
    """
    Get the ordered list of supported subprotocol names from settings.

    Falls back to all available wire formats if the setting is not defined.

    Returns:
        list: Ordered list of subprotocol name strings.

    """
    configured = getattr(settings, "WEBSOCKET_SUBPROTOCOLS", None)
    if configured is None:
        # No explicit configuration; advertise all known wire formats.
        return list(_get_wire_formats().keys())

    # Allow a single string (common misconfiguration) by coercing to a list.
    if isinstance(configured, str):
        protos = [configured]
    else:
        try:
            protos = list(configured)
        except TypeError as err:
            raise TypeError(
                "settings.WEBSOCKET_SUBPROTOCOLS must be a string or an iterable "
                "of strings (e.g. list/tuple); got %r" % (configured,)
            ) from err

    # Warn about any configured names that don't match a known wire format.
    # Unknown names are harmlessly skipped during negotiation (onConnect only
    # selects protocols present in both the client's offer and the registry),
    # but a typo here is almost certainly unintentional.
    wire_formats = _get_wire_formats()
    unknown = [name for name in protos if name not in wire_formats]
    if unknown:
        from evennia.utils import logger

        logger.log_warn(
            "WEBSOCKET_SUBPROTOCOLS contains unknown protocol name(s): %s. "
            "Known protocols: %s"
            % (
                ", ".join(repr(n) for n in unknown),
                ", ".join(repr(n) for n in wire_formats),
            )
        )

    return protos


# Handshake headers kept for the browser fingerprint. Anything outside this list
# is either constant across all browsers or changes on every request.
_FINGERPRINT_HEADERS = (
    "user-agent",
    "accept-language",
    "accept-encoding",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-websocket-extensions",
)


class WebSocketClient(WSProtocolBase, _BASE_SESSION_CLASS):
    """
    Implements the server-side of the Websocket connection.

    Supports multiple wire formats via RFC 6455 subprotocol negotiation.
    The wire format is selected during the WebSocket handshake in onConnect()
    and determines how all subsequent messages are encoded and decoded.

    Attributes:
        wire_format (WireFormat): The selected wire format codec for this
            connection. Set during onConnect().

    """

    # nonce value, used to prevent the webclient from erasing the
    # webclient_authenticated_uid value of csession on disconnect
    nonce = 0

    # Session-continuity state. Class-level defaults, so an instance built
    # without a live handshake (as tests and bus recovery do) reads consistently.
    #: The resume token issued in this connection's last `hello` reply.
    resume_token = None
    #: Registered with the session handler, by a connect or a takeover.
    _session_bound = False
    #: The socket is gone: output is recorded for replay, never written.
    _link_lost = False
    #: Monotonic deadline while the session is held for a reconnect.
    _held_until = None
    #: Another socket took this session over; this object speaks for no one.
    _superseded = False
    #: The session is over and nothing may revive it.
    _ended = False
    _hello_timer = None
    _hold_timer = None
    _opened_at = None
    _lost_at = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protocol_key = "webclient/websocket"
        self.browserstr = ""
        self.wire_format = None

    @staticmethod
    def _enforce_websocket_origin(request):
        """Reject non-empty Origin headers that are not in the allowlist.

        Empty/missing Origin is allowed (matches nginx's ``/ws`` policy and
        non-browser clients). When ``WEBSOCKET_ALLOWED_ORIGINS`` is unset or
        empty, the check is disabled so stock games keep working.
        """
        allowed = getattr(settings, "WEBSOCKET_ALLOWED_ORIGINS", None) or ()
        if not allowed:
            return
        headers = getattr(request, "headers", None) or {}
        origin = (headers.get("origin") or "").strip()
        if not origin:
            return
        if origin in allowed:
            return
        from evennia.utils import logger

        logger.log_warn("WebSocket Origin rejected: %r" % (origin,))
        raise HandshakeDenied("forbidden", status_code=403)

    def onConnect(self, request):
        """
        Called during the WebSocket opening handshake, before onOpen().

        This is where we negotiate the WebSocket subprotocol. The client
        sends a list of subprotocols it supports via Sec-WebSocket-Protocol.
        We select the best match from our supported list.

        When ``settings.WEBSOCKET_ALLOWED_ORIGINS`` is a non-empty iterable,
        a non-empty ``Origin`` header must match one of those values exactly
        (case-sensitive). An empty/missing Origin is allowed so non-browser
        clients and the nginx allowlist stay aligned. A bad Origin raises
        ``HandshakeDenied`` (HTTP 403) with no useful body.

        Args:
            request (ConnectionRequest): The WebSocket connection request,
                containing request.protocols (list of offered subprotocols).

        Returns:
            str or None: The selected subprotocol name to echo back in the
                Sec-WebSocket-Protocol response header, or None if no
                subprotocol was negotiated (legacy client with no header,
                or client offered protocols that don't match).

        """
        self._enforce_websocket_origin(request)

        wire_formats = _get_wire_formats()
        supported = _get_supported_subprotocols()

        if request.protocols:
            # Client offered subprotocols — pick the first one we support
            # (order follows the server's preference from settings)
            for proto_name in supported:
                if proto_name in request.protocols and proto_name in wire_formats:
                    self.wire_format = wire_formats[proto_name]
                    return proto_name

            # Client offered protocols but none matched. Per RFC 6455, if we
            # don't echo a subprotocol, a well-behaved client should close the
            # connection. We still set a wire format so the connection doesn't
            # crash if the client proceeds anyway.
            from evennia.utils import logger

            logger.log_warn(
                "WebSocket client offered subprotocols %r but none match "
                "server's supported list %r. Falling back to v1 format."
                % (request.protocols, supported)
            )
            if "v1.evennia.com" in wire_formats:
                self.wire_format = wire_formats["v1.evennia.com"]
            elif wire_formats:
                self.wire_format = next(iter(wire_formats.values()))
            return None

        # No Sec-WebSocket-Protocol header at all — legacy client.
        # Always use v1 format regardless of WEBSOCKET_SUBPROTOCOLS.
        if "v1.evennia.com" in wire_formats:
            self.wire_format = wire_formats["v1.evennia.com"]
        elif wire_formats:
            self.wire_format = next(iter(wire_formats.values()))

        return None

    def get_client_session(self):
        """
        Get the Client browser session (used for auto-login based on browser session)

        Returns:
            csession (ClientSession): This is a django-specific internal representation
                of the browser session.

        """
        try:
            # client will connect with wsurl?csessid&page_id&browserid
            webarg = self.http_request_uri.split("?", 1)[1]
        except IndexError:
            # this may happen for custom webclients not caring for the
            # browser session.
            self.csessid = None
            return None
        except AttributeError:
            from evennia.utils import logger

            self.csessid = None
            logger.log_trace(str(self))
            return None

        self.csessid, *cargs = webarg.split("&", 2)
        if len(cargs) == 1:
            self.browserstr = str(cargs[0])
        elif len(cargs) == 2:
            self.page_id = str(cargs[0])
            self.browserstr = str(cargs[1])

        if self.csessid:
            return _CLIENT_SESSIONS(session_key=self.csessid)

    def _device_token(self):
        """The signed device token this browser presented, or empty.

        Read from the handshake cookies rather than from the websocket URL: a
        cookie is sent by the browser without the page having to remember to
        put it there, and it is the copy that survives a page the shell did not
        render.
        """
        try:
            from evennia.moderation.device import token_from_headers

            return token_from_headers(getattr(self, "http_headers", None))
        except Exception:
            return ""

    def _collect_http_fingerprint(self):
        """
        Copy the identifying handshake headers into a plain dict.

        Values are truncated -- a header is a client-controlled string and this
        one ends up in the database.

        Returns:
            dict: header name to value, missing headers omitted.

        """
        collected = {}
        try:
            headers = getattr(self, "http_headers", None) or {}
            for name in _FINGERPRINT_HEADERS:
                value = headers.get(name)
                if value:
                    collected[name] = str(value)[:255]
        except Exception:
            # Fingerprint detail is never worth breaking a connection over.
            return {}
        return collected

    def _collect_tls_fingerprint(self):
        """Copy the TLS handshake headers, but only from a trusted proxy.

        These describe a handshake this process never saw: the proxy terminated
        it. That makes them ordinary request headers, and a client can send any
        header it likes.

        So the same gate the forwarded address uses applies here, and for a
        sharper reason. A wrong address is a wrong address; a forged
        fingerprint is a client choosing what staff believe about it, which is
        worse than having no fingerprint at all.

        Returns:
            dict: header name to value, empty when the peer is not trusted.

        """
        try:
            peer = self.transport.getPeer()
            if getattr(peer, "host", None) not in settings.UPSTREAM_IPS:
                return {}
            headers = getattr(self, "http_headers", None) or {}
            from evennia.moderation.capture import TLS_HEADERS

            return {name: str(headers[name])[:512] for name in TLS_HEADERS if headers.get(name)}
        except Exception:
            # Fingerprint detail is never worth breaking a connection over.
            return {}

    def onOpen(self):
        """
        This is called when the WebSocket connection is fully established.

        """
        peer = self.transport.getPeer()
        client_address = getattr(peer, "host", None)
        # Raw TCP peer, kept separate from the forwarded address so that a
        # misconfigured UPSTREAM_IPS is visible downstream instead of silently
        # recording the reverse proxy as every web player's address.
        peer_host = client_address
        xff_applied = False

        if client_address in settings.UPSTREAM_IPS and "x-forwarded-for" in self.http_headers:
            addresses = [x.strip() for x in self.http_headers["x-forwarded-for"].split(",")]
            addresses.reverse()

            for addr in addresses:
                if addr not in settings.UPSTREAM_IPS:
                    client_address = addr
                    xff_applied = True
                    break

        self._peer_host = peer_host
        self._xff_applied = xff_applied
        self._xff_present = "x-forwarded-for" in self.http_headers

        # Sanctioned addresses are dropped here, before the Server ever learns
        # of the connection.
        from evennia.moderation.portal_guard import REFUSAL_TEXT, refuses
        from evennia.moderation.ratelimit import REFUSAL_TEXT as RATE_TEXT
        from evennia.moderation.ratelimit import rate_limited

        if refuses(client_address):
            self.sendClose(CLOSE_NORMAL, REFUSAL_TEXT.strip())
            return

        # The address here is already forwarded-corrected, so a trusted proxy
        # does not collapse every browser onto one counter. An untrusted one
        # leaves the proxy's own address, which the limiter exempts.
        if rate_limited(client_address):
            self.sendClose(CLOSE_NORMAL, RATE_TEXT.strip())
            return

        self.init_session("websocket", client_address, self.factory.sessionhandler)

        csession = self.get_client_session()  # this sets self.csessid
        uid = csession and csession.get("webclient_authenticated_uid", None)
        nonce = csession and csession.get("webclient_authenticated_nonce", 0)
        if uid:
            # the client session is already logged in.
            self.uid = uid
            self.nonce = nonce
            self.logged_in = True
        # A session this browser dropped is no longer swept up here by csessid:
        # it is held for the tab it belongs to, which claims it with its resume
        # token (see _resume), and ends on its own if that tab never returns.

        # Ensure wire_format is set (it should be from onConnect, but
        # in testing scenarios onConnect may not have been called)
        if self.wire_format is None:
            wire_formats = _get_wire_formats()
            self.wire_format = wire_formats.get(
                "v1.evennia.com", next(iter(wire_formats.values()), None)
            )

        if self.wire_format is None:
            from evennia.utils import logger

            logger.log_err("WebSocketClient: No wire formats available. Closing connection.")
            self.sendClose(CLOSE_NORMAL, "No wire formats available")
            return

        self.protocol_flags.update(self._socket_flags())

        # watch for dead links
        self.transport.setTcpKeepAlive(1)
        self._opened_at = time.monotonic()
        _LIVE_SOCKETS.add(self)
        _ensure_keepalive()
        if getattr(self.wire_format, "supports_resume", False):
            # This client's first frame is a `hello` that may claim a held
            # session, and a socket that resumes one must never reach the Server
            # as a new connection. So the connect waits for it, briefly.
            self._hello_timer = _call_later(HELLO_WAIT_SECONDS, self._connect_session)
        else:
            self._connect_session()

    def _socket_flags(self):
        """Protocol flags that describe this socket rather than the session on it.

        A takeover keeps the session's own flags (screen reader, screen size,
        saved options, negotiated capabilities) and replaces only these.

        Returns:
            dict: flag name to value.

        """
        browserstr = f":{self.browserstr}" if self.browserstr else ""
        return {
            "CLIENTNAME": f"Evennia Webclient (websocket{browserstr} [{self.wire_format.name}])",
            "UTF-8": True,
            # Address provenance, synced to the Server so moderation tooling can
            # tell a real client address from an un-rewritten proxy address.
            "PEER_IP": getattr(self, "_peer_host", None),
            "XFF_APPLIED": bool(getattr(self, "_xff_applied", False)),
            "XFF_PRESENT": bool(getattr(self, "_xff_present", False)),
            # Handshake headers the browser sent us. Read once here --
            # http_headers is only populated for the lifetime of the connection
            # and nothing else in the codebase looks at it. Raw values only;
            # hashing happens server-side.
            "HTTP_FP": self._collect_http_fingerprint(),
            "HTTP_ORDER": list(getattr(self, "http_header_order", None) or ())[:64],
            "TLS_FP": self._collect_tls_fingerprint(),
            # page_id is only set when the client sends the three-argument form.
            "BROWSERSTR": str(getattr(self, "browserstr", "") or ""),
            "PAGE_ID": str(getattr(self, "page_id", "") or ""),
            "DEVICE_TOKEN": self._device_token(),
            "OOB": self.wire_format.supports_oob,
            "TRUECOLOR": True,
            "XTERM256": True,
            "ANSI": True,
        }

    def _connect_session(self):
        """Start this socket's session with the Server, once."""
        self._cancel_timer("_hello_timer")
        if self._session_bound or self._link_lost or self._superseded or self._ended:
            return
        self._session_bound = True
        self.sessionhandler.connect(self)

    def _cancel_timer(self, name):
        """Cancel and forget one of this socket's pending timers."""
        timer = getattr(self, name, None)
        if timer is not None:
            setattr(self, name, None)
            try:
                timer.cancel()
            except Exception:
                pass

    def disconnect(self, reason=None):
        """
        Generic hook for the engine to call in order to
        disconnect this protocol.

        This ends the session: the Server has disconnected it, the client asked
        to close, or the browser closed the page. A dropped socket does not come
        here; see ``_link_down``.

        Args:
            reason (str or None): Motivation for the disconnection.

        """
        # The batch drain below can itself discover a dead link and call back in
        # here; one close is enough.
        if getattr(self, "_disconnecting", False):
            return
        self._disconnecting = True
        self._end_session(reason)

    def _end_session(self, reason=None, clear_browser_auth=True):
        """End the session on this socket and tell the Server.

        Args:
            reason (str or None): Close reason for the socket, if it is still up.
            clear_browser_auth (bool): Clear the browser's auto-login stamp, as a
                logout does. A hold that runs out keeps it: the browser never
                asked to leave, and its next visit should find the player still
                signed in.

        """
        if self._ended or self._superseded:
            return
        self._ended = True
        self._held_until = None
        self._cancel_timer("_hold_timer")
        self._cancel_timer("_hello_timer")
        _LIVE_SOCKETS.discard(self)
        if self.resume_token and _RESUMABLE.get(self.resume_token) is self:
            del _RESUMABLE[self.resume_token]

        if clear_browser_auth:
            csession = self.get_client_session()
            # Portal-wide shutdown (deploy reboot) must not wipe the Django
            # session auto-login stamp: the webclient reconnect loop relies on it.
            preserve_auth = getattr(getattr(self, "sessionhandler", None), "_disconnect_all", False)
            if csession and not preserve_auth:
                # if the nonce is different, webclient_authenticated_uid has been
                # set *before* this disconnect (disconnect called after a new
                # client connects, which occurs in some 'fast' browsers like
                # Google Chrome and Mobile Safari)
                if csession.get("webclient_authenticated_nonce", 0) == self.nonce:
                    csession["webclient_authenticated_uid"] = None
                    csession["webclient_authenticated_nonce"] = 0
                    csession.save()
                self.logged_in = False

        if self._session_bound:
            self.sessionhandler.disconnect(self)
        if not self._link_lost:
            # Batched frames are flushed on the next loop iteration, so a burst
            # queued in this same iteration (e.g. the `logout` OOB that precedes
            # a server-side quit) would be written after the close and lost.
            # Drain it first.
            self._flush_batch()
            # RFC 6455 close codes: 1000 normal, 1001 browser window closed,
            # 3000-4999 application-specific (CLOSE_SUPERSEDED is one).
            self.sendClose(CLOSE_NORMAL, reason)

    def _link_down(self, code=None, clean=False, cause="closed"):
        """The socket is gone; decide what becomes of the session on it.

        Runs once per socket: from ``onClose``, or from the first write or
        keepalive check that finds the socket dead, whichever comes first.

        Args:
            code (int or None): Close code the peer sent, if it sent one.
            clean (bool): Whether the WebSocket closing handshake completed.
            cause (str): What noticed the loss, for the log.

        """
        if self._link_lost:
            return
        self._link_lost = True
        self._lost_at = time.monotonic()
        _LIVE_SOCKETS.discard(self)
        self._cancel_timer("_hello_timer")
        if self._superseded or self._ended:
            return
        # Anything still queued for coalescing must reach the replay buffer
        # before the session is held, or a reconnect replays a hole.
        if getattr(self, "_batch_pending", None):
            try:
                self._flush_batch()
            except Exception:
                logger.log_trace("webclient: flushing a batch on link loss")
        if code in (CLOSE_NORMAL, GOING_AWAY):
            # GOING_AWAY (1001) is an ordinary browser tab-close/navigation, so it
            # must clear the auto-login stamp like a normal close. A portal reboot
            # keeps the stamp via the _disconnect_all guard in _end_session().
            self._log_link_down(code, clean, cause, "session ended")
            self.disconnect()
        elif not self._session_bound:
            # Gone before its session started: there is nothing to hold or end.
            self._ended = True
            self._log_link_down(code, clean, cause, "dropped before hello")
        elif self._hold():
            self._log_link_down(
                code, clean, cause, "held %ds for a reconnect" % round(_resume_grace())
            )
        else:
            self._log_link_down(code, clean, cause, "session ended")
            self._end_session(clear_browser_auth=False)

    def _log_link_down(self, code, clean, cause, outcome):
        """Record why a socket closed and what happened to its session.

        Close codes are the only evidence of what broke a connection: a peer
        that sent 1000/1001 left on purpose, a missing code means the link just
        died, and a keepalive timeout means it died without a word.
        """
        now = time.monotonic()
        age = now - self._opened_at if self._opened_at else 0.0
        heard = now - getattr(self, "last_received", now)
        logger.log_info(
            "webclient: link %s for session %s (uid %s) after %ds: code %s%s, last heard"
            " %ds ago; %s"
            % (
                cause,
                getattr(self, "sessid", None),
                getattr(self, "uid", None),
                age,
                code,
                "" if clean else ", unclean",
                heard,
                outcome,
            )
        )

    def _hold(self):
        """Keep this session for its tab to reclaim, if it can be reclaimed.

        Only a signed-in session that was issued a resume token can be claimed
        back, so nothing else is held: an anonymous login screen is cheaper to
        redraw than to keep.

        Returns:
            bool: Whether the session is now held.

        """
        grace = _resume_grace()
        token = self.resume_token
        if (
            not grace
            or not token
            or _RESUMABLE.get(token) is not self
            or not (self.logged_in and self.uid)
            or getattr(self, "_disconnecting", False)
        ):
            return False
        self._held_until = time.monotonic() + grace
        self._hold_timer = _call_later(grace, self._expire_hold, "grace expired")
        _enforce_hold_limits()
        return not self._ended

    def _expire_hold(self, why="grace expired"):
        """End a held session nobody came back for.

        Args:
            why (str): What ended the hold, for the log.

        """
        if self._held_until is None or self._superseded or self._ended:
            return
        logger.log_info(
            "webclient: session %s (uid %s) was not reclaimed (%s); ending it"
            % (self.sessid, self.uid, why)
        )
        self._end_session(clear_browser_auth=False)

    def _keepalive_tick(self, now, timeout):
        """Ping this socket, or drop it if it has been silent past ``timeout``.

        Args:
            now (float): Monotonic time of this sweep.
            timeout (float): Seconds of silence after which the peer is gone.

        """
        if self._link_lost or self._superseded or self._ended:
            _LIVE_SOCKETS.discard(self)
            return
        if now - self.last_received > timeout:
            # Abort rather than close: a graceful close waits to flush to a peer
            # that is not reading, which is the problem being handled.
            self.abortConnection()
            self._link_down(cause="silent past the keepalive timeout")
        else:
            self.sendPing()

    def _write(self, data, is_binary=False):
        """Put one frame on the wire, unless the wire is gone.

        A held or dead socket writes nothing: its frames are already in the
        replay buffer, which is where a reconnect finds them.

        Args:
            data (bytes): The encoded frame.
            is_binary (bool): Send as a BINARY frame.

        """
        if self._link_lost or self._superseded:
            return None
        try:
            return self.sendMessage(data, isBinary=is_binary)
        except Disconnected:
            # The socket closed under us: the browser sent a close frame this
            # session has not acted on yet, or the link died. Either way it is a
            # lost link, not a logout.
            self._link_down(cause="found closed on write")
            return None

    def _handle_client_hello(self, raw):
        """Handle the client's ``hello``: resume a held session or start one, then answer.

        The reply is what re-bases the client's sequence counter. Without it a
        client that reconnects after its hold expired keeps asking to resume
        from a seq the server's fresh counter will never reach again, and
        replay silently stops working for that browser forever. It also carries
        the resume token for the client's next reconnect.

        Args:
            raw (dict): the decoded client ``hello`` envelope.

        """
        resume = raw.get("resume")
        resumed = self._resume(resume if isinstance(resume, dict) else {})
        if resumed is None:
            self._connect_session()
        reply = {
            "t": "hello",
            "protocol": "azaban.v1",
            "resumed": resumed is not None,
            "token": self._issue_resume_token(),
        }
        if resumed and resumed["gap"]:
            reply["gap"] = True
        # Stamped last, so its seq is above every frame replayed above it and
        # the client can assign (not max) its cursor from it.
        self.sendLine(reply)

    def _issue_resume_token(self):
        """Give this connection a fresh resume token and retire its old one.

        Returns:
            str: The new token.

        """
        old = self.resume_token
        if old and _RESUMABLE.get(old) is self:
            del _RESUMABLE[old]
        token = secrets.token_urlsafe(24)
        self.resume_token = token
        _RESUMABLE[token] = self
        return token

    def _reset_resume_buffer(self):
        """Drop the replay window this socket is holding.

        The client's "clear buffer" only empties its own scrollback array. The
        portal keeps every frame it has sent, and a page reload asks to resume
        from seq 0 - a fresh page has no cursor to present - so the whole window
        replayed straight back into the log the player had just cleared, and the
        clear looked like it had silently undone itself. Clearing here is what
        makes it stick. The seq counter runs on.

        """
        buf = getattr(self, "out_buffer", None)
        if buf is not None:
            buf.clear()

    def _resume(self, resume):
        """Take over the session a reconnecting tab asks for, and replay its gap.

        Args:
            resume (dict): the ``resume`` block of the client's hello: the
                ``token`` its last connection was issued and the ``last_seq`` it
                applied.

        Returns:
            dict or None: ``{"replayed": int, "gap": bool}`` when this socket now
                carries the session; None when it starts a new one.

        """
        token = resume.get("token")
        if not isinstance(token, str) or not token or self._session_bound:
            # No claim, or this socket already started a session of its own (its
            # hello came after HELLO_WAIT_SECONDS): adopting now would orphan it.
            return None
        _enforce_hold_limits()
        old = _RESUMABLE.get(token)
        if old is None or old is self or old._ended or old._superseded:
            return None
        if not (self.uid and old.logged_in and old.uid == self.uid):
            # The token is not enough: the browser must be signed in as the
            # account the session belongs to, or holding a token would be
            # enough to take over someone's session and read its output.
            logger.log_warn(
                "webclient: resume of session %s refused: this browser is not signed in"
                " as its account" % old.sessid
            )
            return None
        handler = self.sessionhandler
        if not handler.owns(old):
            # Nothing on the Server to resume; the old socket is a leftover.
            old._end_session(clear_browser_auth=False)
            return None
        offline = time.monotonic() - old._lost_at if old._lost_at else 0.0
        self._adopt(old)
        replayed, gap = self._replay_since(resume.get("last_seq"))
        logger.log_info(
            "webclient: session %s (uid %s) resumed on a new connection after %.1fs;"
            " replayed %d frame(s)%s"
            % (self.sessid, self.uid, offline, replayed, ", with a gap" if gap else "")
        )
        return {"replayed": replayed, "gap": gap}

    def _adopt(self, old):
        """Move ``old``'s session onto this socket, in place.

        The Server keeps its session object: the sessid and the bus socket
        incarnation carry over, the handler entry is swapped, and only the socket
        facts that changed (address, headers, flags) are synced across.

        Args:
            old (WebSocketClient): The held, or still apparently open, socket
                that owned the session until now.

        """
        was_live = not old._link_lost
        # Settle the old socket first. What it had queued goes to the buffer
        # that moves across, and from here on it writes nothing and its late
        # callbacks (a close, a timer, a frame) change nothing.
        old._link_lost = True
        _LIVE_SOCKETS.discard(old)
        if getattr(old, "_batch_pending", None):
            old._flush_batch()
        old._cancel_timer("_hold_timer")
        old._cancel_timer("_hello_timer")
        old._held_until = None
        old._superseded = True
        if old.resume_token and _RESUMABLE.get(old.resume_token) is old:
            del _RESUMABLE[old.resume_token]

        for attr in _SESSION_STATE:
            if hasattr(old, attr):
                setattr(self, attr, getattr(old, attr))
        flags = dict(old.protocol_flags or {})
        flags.update(self._socket_flags())
        self.protocol_flags = flags
        self.out_seq = getattr(old, "out_seq", 0)
        self.out_buffer = getattr(old, "out_buffer", None) or deque()
        old.out_buffer = deque()
        self._cancel_timer("_hello_timer")
        self._session_bound = True
        self.sessionhandler.rebind(old, self)

        if was_live:
            # The old socket still looked open: a phone that changed networks
            # before the keepalive noticed, or a copied tab. Say why it is being
            # closed, then cut it loose without waiting on a peer that may be gone.
            old.sendClose(CLOSE_SUPERSEDED, "session resumed on another connection")
            old.abortConnection()

    def _replay_since(self, last_seq):
        """Resend buffered frames the client has not applied.

        Args:
            last_seq (int or None): The last seq the client applied.

        Returns:
            tuple: ``(replayed, gap)``: how many frames were resent, and whether
                some it missed had already been evicted from the buffer.

        """
        try:
            last_seen = max(0, int(last_seq or 0))
        except (TypeError, ValueError):
            last_seen = 0
        buf = getattr(self, "out_buffer", None) or ()
        out_seq = getattr(self, "out_seq", 0)
        oldest = buf[0][0] if buf else out_seq + 1
        # A reloaded page has no cursor (0) and is not missing anything it
        # could have had; only a client that was following along has a gap.
        gap = 0 < last_seen < out_seq and oldest > last_seen + 1
        replayed = 0
        # Replayed frames already carry their seq, so they bypass sendLine.
        for seq, frame in list(buf):
            if seq > last_seen:
                self._write(frame.encode("utf-8"))
                replayed += 1
        return replayed, gap

    def _send_pong(self, raw):
        """Answer the client's heartbeat at the Portal.

        Portal-local like ``hello``: never stamped, buffered, or replayed, and
        never sent on to the Server. The client uses it to notice a socket that
        died without the browser being told.

        Args:
            raw (dict): The client's ``ping`` envelope.

        """
        pong = {"t": "pong"}
        nonce = raw.get("n")
        if isinstance(nonce, int) and not isinstance(nonce, bool):
            pong["n"] = nonce
        self._write(json.dumps(pong).encode("utf-8"))

    def onClose(self, wasClean, code=None, reason=None):
        """
        This is executed when the connection is lost for whatever
        reason. it can also be called directly, from the disconnect
        method.

        Args:
            wasClean (bool): ``True`` if the WebSocket was closed cleanly.
            code (int or None): Close status as sent by the WebSocket peer.
            reason (str or None): Close reason as sent by the WebSocket peer.

        """
        self._link_down(code=code, clean=wasClean)

    def onMessage(self, payload, isBinary):
        """
        Callback fired when a complete WebSocket message was received.

        Delegates to the active wire format's decode_incoming() method
        to parse the message into kwargs for data_in().

        Args:
            payload (bytes): The WebSocket message received.
            isBinary (bool): Flag indicating whether payload is binary or
                             UTF-8 encoded text.

        """
        if self._superseded or self._ended:
            # A late frame from a socket whose session moved on, or ended,
            # speaks for no one.
            return
        # Peek for a resume request in the client's hello (before decoding), so
        # we can replay missed frames at the portal without involving the server.
        if not isBinary:
            try:
                raw = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                raw = None
            kind = raw.get("t") if isinstance(raw, dict) else None
            if kind == "hello":
                self._handle_client_hello(raw)
            elif kind == "resume_reset":
                # Client-side scrollback wipe. Portal-local, like `hello`: the
                # server has no say in what this browser keeps on screen.
                self._reset_resume_buffer()
            elif kind == "ping":
                # Transport heartbeat: it neither starts a session nor reaches one.
                self._send_pong(raw)
                return
        # Any frame but a `hello` means this client is not resuming anything.
        self._connect_session()

        if self.wire_format:
            kwargs = self.wire_format.decode_incoming(
                payload, isBinary, protocol_flags=self.protocol_flags
            )
            if kwargs:
                self.data_in(**kwargs)
        else:
            # Fallback: try legacy JSON parsing
            try:
                cmdarray = json.loads(str(payload, "utf-8"))
                if cmdarray:
                    self.data_in(**{cmdarray[0]: [cmdarray[1], cmdarray[2]]})
            except (json.JSONDecodeError, UnicodeDecodeError, IndexError):
                pass

    def _stamp_and_buffer(self, frame):
        """Stamp a monotonic ``s`` seq onto a JSON frame and buffer it for resume.

        Args:
            frame (dict or str): the envelope. A dict is stamped as-is: the
                wire format handed us the object, so there is nothing to parse.
                A str is decoded first, for formats that serialize their own
                frames (and for anything already on the wire, like a replay).

        Returns:
            str: the serialized, stamped frame, or the input unchanged if it was
                not a JSON object.

        """
        obj = frame
        if not isinstance(obj, dict):
            try:
                obj = json.loads(frame)
            except (json.JSONDecodeError, TypeError, ValueError):
                return frame
            if not isinstance(obj, dict):
                return frame
        next_seq = getattr(self, "out_seq", 0) + 1
        obj = dict(obj)
        obj["s"] = next_seq
        # ASCII escaping makes replay string length equal its encoded byte size.
        line = json.dumps(obj, ensure_ascii=True)
        _check_outgoing_size(line)
        self.out_seq = next_seq
        buf = getattr(self, "out_buffer", None)
        if buf is None:
            buf = self.out_buffer = deque()
        buf.append((self.out_seq, line))
        _trim_replay_frames(buf)
        return line

    def sendLine(self, line):
        """
        Send data to client.

        Args:
            line (str or dict): Text to send. A dict is treated as a JSON
                envelope and serialized once, after stamping.

        """
        if getattr(self, "_batch_pending", None):
            self._serialize_stamped(line, getattr(self, "out_seq", 0) + 2)
            self._flush_batch()
        line = self._stamp_and_buffer(line)
        _check_outgoing_size(line)
        return self._write(line.encode())

    def sendEncoded(self, data, is_binary=False):
        """
        Send encoded data to the client.

        Text frames from a resume-capable wire format are sequence-stamped and
        buffered here, exactly as ``sendLine`` does for the legacy path. Without
        this, a subprotocol that encodes its own frames would never populate the
        resume buffer and reconnects would silently replay nothing.

        Args:
            data (bytes or dict): The data to send. A resume-capable format may
                hand over the envelope dict itself (see
                ``WireFormat.supports_resume``); it is stamped and serialized
                exactly once here rather than being parsed back out of bytes.
            is_binary (bool): If True, send as a BINARY frame.
                If False, send as a TEXT frame.

        """
        if not is_binary and getattr(self.wire_format, "supports_resume", False):
            frame = data
            if isinstance(frame, (bytes, bytearray)):
                try:
                    frame = frame.decode("utf-8")
                except UnicodeDecodeError:
                    frame = None
            if frame is not None:
                if self._batching_enabled():
                    return self._queue_batched(frame)
                data = self._stamp_and_buffer(frame).encode("utf-8")
        if isinstance(data, dict):
            # Not resume-capable, or binary: still has to reach the wire as bytes.
            data = json.dumps(data).encode("utf-8")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        _check_outgoing_size(data)
        return self._write(data, is_binary=is_binary)

    def _batching_enabled(self):
        """
        Whether this client asked for coalesced frames in its ``hello`` caps.

        Returns:
            bool: True if bursts should be batched.
        """
        caps = (self.protocol_flags or {}).get("AZABAN_CAPS") or {}
        return isinstance(caps, dict) and bool(caps.get("batching"))

    def _queue_batched(self, frame):
        """
        Hold a frame for the current reactor iteration and schedule a flush.

        Args:
            frame (dict or str): the JSON envelope, unserialized where the wire
                format handed one over.

        """
        buf = getattr(self, "_batch_pending", None)
        if buf is None:
            buf = self._batch_pending = []
        current_seq = getattr(self, "out_seq", 0)
        target = min(
            _setting_bytes("WEBSOCKET_BATCH_BYTES", _DEFAULT_BATCH_BYTES),
            _setting_bytes("WEBSOCKET_MAX_OUTGOING_BYTES", _DEFAULT_MAX_OUTGOING_BYTES),
        )
        try:
            member = frame if isinstance(frame, dict) else json.loads(frame)
            if not isinstance(member, dict):
                raise TypeError
        except (json.JSONDecodeError, TypeError, ValueError):
            self._serialize_stamped(frame, current_seq + (2 if buf else 1))
            if buf:
                self._flush_batch()
            self._batch_pending = [frame]
            return self._flush_batch()

        candidate = [*buf, member]
        payload = candidate[0] if len(candidate) == 1 else {"t": "batch", "frames": candidate}
        stamped = dict(payload)
        stamped["s"] = current_seq + 1
        if len(candidate) <= BATCH_MAX_FRAMES and _frame_bytes(stamped) <= target:
            self._serialize_stamped(payload, current_seq + 1)
            buf.append(member)
        else:
            future_seq = current_seq + (2 if buf else 1)
            serialized = self._serialize_stamped(member, future_seq)
            if buf:
                self._flush_batch()
            if _frame_bytes(serialized) > target:
                self._batch_pending = [member]
                return self._flush_batch()
            buf = self._batch_pending = [member]
        if len(buf) >= BATCH_MAX_FRAMES:
            return self._flush_batch()
        if not getattr(self, "_batch_scheduled", False):
            self._batch_scheduled = True
            try:
                asyncio.get_running_loop().call_soon(self._flush_batch)
            except RuntimeError:
                # No running loop (tests, shutdown): send straight through.
                self._batch_scheduled = False
                return self._flush_batch()

    def _flush_batch(self):
        """Send queued envelopes in ordered, byte-bounded groups."""
        self._batch_scheduled = False
        buf = getattr(self, "_batch_pending", None)
        if not buf:
            return
        self._batch_pending = []
        try:
            members = [frame if isinstance(frame, dict) else json.loads(frame) for frame in buf]
            if not all(isinstance(frame, dict) for frame in members):
                raise TypeError
        except (json.JSONDecodeError, TypeError, ValueError):
            members = None
        if members is None:
            payloads = buf
        else:
            payloads = []
            group = []
            target = min(
                _setting_bytes("WEBSOCKET_BATCH_BYTES", _DEFAULT_BATCH_BYTES),
                _setting_bytes("WEBSOCKET_MAX_OUTGOING_BYTES", _DEFAULT_MAX_OUTGOING_BYTES),
            )
            for member in members:
                candidate = [*group, member]
                payload = (
                    candidate[0] if len(candidate) == 1 else {"t": "batch", "frames": candidate}
                )
                stamped = dict(payload)
                stamped["s"] = getattr(self, "out_seq", 0) + len(payloads) + 1
                size = _frame_bytes(stamped)
                if group and size > target:
                    payloads.append(
                        group[0] if len(group) == 1 else {"t": "batch", "frames": group}
                    )
                    group = [member]
                else:
                    group = candidate
            if group:
                payloads.append(group[0] if len(group) == 1 else {"t": "batch", "frames": group})
        for payload in payloads:
            # Stamped and buffered even once the link is gone, so a reconnect
            # replays the whole burst.
            self._write(self._stamp_and_buffer(payload).encode("utf-8"))

    def _serialize_stamped(self, frame, seq):
        """Serialize one JSON envelope with a prospective sequence stamp."""
        obj = frame
        if not isinstance(obj, dict):
            try:
                obj = json.loads(frame)
            except (json.JSONDecodeError, TypeError, ValueError):
                _check_outgoing_size(frame)
                return frame
            if not isinstance(obj, dict):
                _check_outgoing_size(frame)
                return frame
        obj = dict(obj)
        obj["s"] = seq
        line = json.dumps(obj)
        _check_outgoing_size(line)
        return line

    def at_login(self):
        """Persist normal login through the same browser auth mirror as recovery."""
        self.at_auth_sync()

    def at_auth_sync(self):
        """Repair the browser login stamp without replaying login hooks."""
        csession = self.get_client_session()
        uid = self.uid if self.logged_in else None
        if (
            csession
            and csession.get("webclient_authenticated_nonce", 0) == self.nonce
            and csession.get("webclient_authenticated_uid") != uid
        ):
            csession["webclient_authenticated_uid"] = uid
            csession.save()

    def data_in(self, **kwargs):
        """
        Data User > Evennia.

        Args:
            text (str): Incoming text.
            kwargs (any): Options from protocol.

        Notes:
            At initilization, the client will send the special
            'csessid' command to identify its browser session hash
            with the Evennia side.

            The websocket client will also pass 'websocket_close' command
            to report that the client has been closed and that the
            session should be disconnected.

            Both those commands are parsed and extracted already at
            this point.

        """
        if self._superseded or self._ended:
            return
        if "websocket_close" in kwargs:
            self.disconnect()
            return

        self.sessionhandler.data_in(self, **kwargs)

    def send_text(self, *args, **kwargs):
        """
        Send text data. Delegates to the active wire format's encode_text()
        method, which handles ANSI processing and framing. The exact output
        depends on the negotiated subprotocol (e.g., HTML for v1.evennia.com,
        raw ANSI for MUD Standards formats).

        Args:
            text (str): Text to send.

        Keyword Args:
            options (dict): Options-dict with the following keys understood:
                - raw (bool): No parsing at all (leave ansi markers unparsed).
                - nocolor (bool): Clean out all color.
                - screenreader (bool): Use Screenreader mode.
                - send_prompt (bool): Send as a prompt instead of regular text.

        """
        if self.wire_format:
            result = self.wire_format.encode_text(
                *args, protocol_flags=self.protocol_flags, **kwargs
            )
            if result is not None:
                data, is_binary = result
                self.sendEncoded(data, is_binary=is_binary)
        else:
            # Fallback: legacy behavior
            self._send_text_legacy(*args, **kwargs)

    def _send_text_legacy(self, *args, **kwargs):
        """
        Legacy send_text fallback for when no wire format is set.

        Performs the original Evennia HTML conversion (parse_html) and
        sends a JSON array ``["text", [html_string], {}]`` via sendLine.

        """
        import html as html_lib
        import re

        from evennia.utils.ansi import parse_ansi
        from evennia.utils.text2html import parse_html

        if args:
            args = list(args)
            text = args[0]
            if text is None:
                return
        else:
            return
        flags = self.protocol_flags
        options = kwargs.pop("options", {})
        raw = options.get("raw", flags.get("RAW", False))
        client_raw = options.get("client_raw", False)
        nocolor = options.get("nocolor", flags.get("NOCOLOR", False))
        screenreader = options.get("screenreader", flags.get("SCREENREADER", False))
        prompt = options.get("send_prompt", False)
        _RE = re.compile(r"%s" % settings.SCREENREADER_REGEX_STRIP, re.DOTALL + re.MULTILINE)
        if screenreader:
            text = parse_ansi(text, strip_ansi=True, xterm256=False, mxp=False)
            text = _RE.sub("", text)
        cmd = "prompt" if prompt else "text"
        if raw:
            if client_raw:
                # client_raw=True bypasses both ANSI->HTML conversion and HTML
                # escaping. The webclient's default_out plugin renders text via
                # jQuery .html()/string concat, so any unescaped <, >, & here
                # becomes live DOM. Only set this for content that is already
                # known-safe HTML produced by trusted server code, never for
                # anything that touches player input.
                args[0] = text
            else:
                args[0] = html_lib.escape(text)
        else:
            args[0] = parse_html(text, strip_ansi=nocolor)
        self.sendLine(json.dumps([cmd, args, kwargs]))

    def send_prompt(self, *args, **kwargs):
        """
        Send a prompt to the client.

        Prompts are handled separately from regular text because some
        wire formats (e.g. json.mudstandards.org) send prompts as a
        distinct message type that the client can render differently.

        Args:
            *args: Prompt text as first arg.

        Keyword Args:
            options (dict): Same options as send_text.

        """
        if self.wire_format:
            result = self.wire_format.encode_prompt(
                *args, protocol_flags=self.protocol_flags, **kwargs
            )
            if result is not None:
                data, is_binary = result
                self.sendEncoded(data, is_binary=is_binary)
        else:
            kwargs.setdefault("options", {}).update({"send_prompt": True})
            self.send_text(*args, **kwargs)

    def send_default(self, cmdname, *args, **kwargs):
        """
        Data Evennia -> User.

        Args:
            cmdname (str): The first argument will always be the oob cmd name.
            *args (any): Remaining args will be arguments for `cmd`.

        Keyword Args:
            options (dict): These are ignored for oob commands. Use command
                arguments (which can hold dicts) to send instructions to the
                client instead.

        """
        if self.wire_format:
            result = self.wire_format.encode_default(
                cmdname, *args, protocol_flags=self.protocol_flags, **kwargs
            )
            if result is not None:
                data, is_binary = result
                self.sendEncoded(data, is_binary=is_binary)
        else:
            # Fallback: legacy behavior
            if not cmdname == "options":
                self.sendLine(json.dumps([cmdname, args, kwargs]))


class AsyncioWebSocketProtocol(asyncio.Protocol):
    """Run a ``WebSocketClient`` (full webclient session) on a native asyncio loop.

    Composition mirror of ``AsyncioTelnetProtocol``: owns a ``WebSocketClient``,
    hands it an ``AsyncioTransportShim``, and forwards the loop's transport
    callbacks. The wsproto core is transport-agnostic and the session layer only
    touches the (shimmed) transport, so the webclient runs unchanged. Use with
    ``loop.create_server(lambda: AsyncioWebSocketProtocol(factory), ...)`` where
    ``factory`` carries ``.sessionhandler`` (as ``WSServerFactory`` does).
    """

    def __init__(self, factory):
        self._factory = factory
        self.ws = None

    def connection_made(self, transport):
        proto_class = getattr(self._factory, "protocol", None) or WebSocketClient
        proto = proto_class()
        proto.factory = self._factory
        proto.transport = AsyncioTransportShim(transport)
        self.ws = proto
        # server role: onConnect/onOpen fire once the client's upgrade arrives

    def data_received(self, data):
        """Run handshake and frame callbacks in an owned database scope."""
        clock.run_callback(self.ws.dataReceived, data, _task_kind="system")

    def connection_lost(self, exc):
        """Release database state opened by protocol disconnect hooks."""
        if self.ws is not None:
            clock.run_callback(
                self.ws.connectionLost, str(exc) if exc else None, _task_kind="system"
            )
