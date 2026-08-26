"""Asyncio HTTP reverse proxy for the Portal (T3 replacement for twisted.web).

The Portal is the public HTTP front: it listens on the proxy port and forwards
every browser request to the Server's internal web port (uvicorn, since the ASGI
migration), streaming the response back. Today that is done with
``twisted.web.proxy.ReverseProxyResource`` on the reactor; this is the same job
on a native asyncio loop, so no reactor is involved.

No hand-rolled HTTP: ``h11`` (sans-io) parses the inbound request and frames the
outbound response; ``httpx.AsyncClient`` speaks to the upstream. ``asyncio.
start_server`` gives a simple ``await``-able request/response loop with keep-alive.

Coexists with the twisted.web proxy (gated); flip the Portal to this once the
asyncio reactor is the norm. Peer IP is preserved via ``X-Forwarded-For`` exactly
as the old ``HTTPChannelWithXForwardedFor`` did.
"""

import asyncio

import h11
import httpx

from evennia.utils import logger

# Hop-by-hop headers must not be forwarded (RFC 7230 6.1).
_HOP_BY_HOP = frozenset(
    b"connection keep-alive proxy-authenticate proxy-authorization te trailers "
    b"transfer-encoding upgrade".split()
)

_MAX_HEADER = 65536


class _ClientGone(Exception):
    """The browser's socket died while we were answering it.

    Raised only by :meth:`ReverseProxy._write`, so it always means the
    *downstream* peer left -- never that the upstream Server misbehaved. A
    closed tab, a navigation away, or an aborted long-lived stream all end a
    request this way, so it is a normal end of connection rather than a fault.
    """


class ReverseProxy:
    """Forward inbound HTTP to a single upstream ``host:port``."""

    def __init__(self, upstream_host, upstream_port):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self._client = None
        self._server = None
        self._stop_lock = asyncio.Lock()

    async def start(self, host, port):
        self._client = httpx.AsyncClient(
            base_url=f"http://{self.upstream_host}:{self.upstream_port}",
            timeout=60,
        )
        self._server = await asyncio.start_server(self.handle_connection, host, port)
        return self._server

    async def stop(self):
        """Close listener and client once while preserving sibling cleanup."""

        async with self._stop_lock:
            server, self._server = self._server, None
            client, self._client = self._client, None
            errors = []
            if server is not None:
                try:
                    server.close()
                    await server.wait_closed()
                except Exception as err:
                    errors.append(err)
            if client is not None:
                try:
                    await client.aclose()
                except Exception as err:
                    errors.append(err)
            if errors:
                raise ExceptionGroup("web proxy cleanup failed", errors)

    async def handle_connection(self, reader, writer):
        conn = h11.Connection(h11.SERVER)
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else None
        try:
            while True:
                request, body = await self._read_request(conn, reader)
                if request is None:
                    break
                if not await self._forward_and_respond(conn, writer, request, body, peer_ip):
                    break
                if conn.our_state is h11.MUST_CLOSE or conn.their_state is h11.MUST_CLOSE:
                    break
                conn.start_next_cycle()
        except OSError:
            # The client reset the socket while we waited for its next request.
            pass
        except Exception:
            logger.log_trace("web proxy: connection error")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _read_request(self, conn, reader):
        """Pull one full request (h11) off the socket; (None, None) on clean close."""
        body = bytearray()
        request = None
        while True:
            event = conn.next_event()
            if event is h11.NEED_DATA:
                data = await reader.read(_MAX_HEADER)
                conn.receive_data(data)  # b"" signals EOF to h11
                if not data and conn.their_state is h11.CLOSED:
                    return None, None
                continue
            if isinstance(event, h11.Request):
                request = event
            elif isinstance(event, h11.Data):
                body += event.data
            elif isinstance(event, h11.EndOfMessage):
                return request, bytes(body)
            elif isinstance(event, h11.ConnectionClosed) or event is h11.PAUSED:
                return None, None

    async def _forward_and_respond(self, conn, writer, request, body, peer_ip):
        """Proxy one request/response exchange.

        Args:
            conn (h11.Connection): Framing for the client-facing connection.
            writer (asyncio.StreamWriter): The client-facing socket.
            request (h11.Request): The request to forward.
            body (bytes): The request body, empty when there is none.
            peer_ip (str or None): The client address, for `X-Forwarded-For`.

        Returns:
            bool: Whether the connection may carry another exchange. False
                once either end has broken, in which case the failure has
                already been reported (or deliberately not, for a hangup).
        """

        fwd_headers = self._request_headers(request, peer_ip)
        target = request.target.decode("latin1")
        response_started = False
        try:
            async with self._client.stream(
                request.method.decode("latin1"),
                target,
                headers=fwd_headers,
                content=body or None,
            ) as upstream:
                out_headers = self._response_headers(upstream)
                await self._write(
                    writer,
                    conn.send(
                        h11.Response(status_code=upstream.status_code, headers=out_headers)
                    ),
                )
                response_started = True

                async for chunk in upstream.aiter_raw():
                    if chunk:
                        await self._write(writer, conn.send(h11.Data(data=chunk)))

                await self._write(writer, conn.send(h11.EndOfMessage()))
        except _ClientGone:
            # Nobody is listening any more. Long-lived streams end no other
            # way -- the feed never closes itself -- so logging a traceback
            # here would turn every closed console tab into an error.
            return False
        except Exception:
            logger.log_trace("web proxy: upstream request failed")
            if not response_started:
                await self._send_error(conn, writer, 502, b"Bad Gateway")
            return False
        return True

    async def _write(self, writer, data):
        """Push one framed chunk to the client.

        Args:
            writer (asyncio.StreamWriter): The client-facing socket.
            data (bytes): Wire bytes produced by ``h11``.

        Raises:
            _ClientGone: The client's socket is no longer writable.
        """

        try:
            writer.write(data)
            await writer.drain()
        except OSError as err:
            raise _ClientGone() from err

    def _request_headers(self, request, peer_ip):
        headers = []
        seen_xff = None
        for name, value in request.headers:
            lname = name.lower()
            if lname in _HOP_BY_HOP or lname == b"host":
                continue
            if lname == b"x-forwarded-for":
                seen_xff = value
                continue
            headers.append((name, value))
        # preserve/extend the forwarded chain, as the old proxy did
        if peer_ip:
            chain = (seen_xff + b", " + peer_ip.encode()) if seen_xff else peer_ip.encode()
            headers.append((b"X-Forwarded-For", chain))
        return headers

    def _response_headers(self, upstream):
        headers = []
        for name, value in upstream.headers.raw:
            if name.lower() in _HOP_BY_HOP:
                continue
            headers.append((name, value))
        return headers

    async def _send_error(self, conn, writer, code, reason):
        """Answer a failed exchange with a minimal plain-text page.

        Stays silent when the client has gone too: there is nobody left to
        tell, and the upstream failure has already been logged.

        Args:
            conn (h11.Connection): Framing for the client-facing connection.
            writer (asyncio.StreamWriter): The client-facing socket.
            code (int): The HTTP status code to send.
            reason (bytes): One line of body text.
        """

        try:
            body = reason + b"\n"
            frames = conn.send(
                h11.Response(
                    status_code=code,
                    headers=[
                        (b"content-length", str(len(body)).encode()),
                        (b"content-type", b"text/plain; charset=utf-8"),
                    ],
                )
            )
            frames += conn.send(h11.Data(data=body))
            frames += conn.send(h11.EndOfMessage())
            await self._write(writer, frames)
        except Exception:
            pass
