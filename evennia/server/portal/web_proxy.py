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
                await self._forward_and_respond(conn, writer, request, body, peer_ip)
                if conn.our_state is h11.MUST_CLOSE or conn.their_state is h11.MUST_CLOSE:
                    break
                conn.start_next_cycle()
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
                writer.write(
                    conn.send(h11.Response(status_code=upstream.status_code, headers=out_headers))
                )
                response_started = True
                await writer.drain()

                async for chunk in upstream.aiter_raw():
                    if chunk:
                        writer.write(conn.send(h11.Data(data=chunk)))
                        await writer.drain()

                writer.write(conn.send(h11.EndOfMessage()))
                await writer.drain()
        except Exception:
            logger.log_trace("web proxy: upstream request failed")
            if response_started:
                raise
            self._send_error(conn, writer, 502, b"Bad Gateway")
            await writer.drain()

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

    def _send_error(self, conn, writer, code, reason):
        try:
            body = reason + b"\n"
            writer.write(
                conn.send(
                    h11.Response(
                        status_code=code,
                        headers=[
                            (b"content-length", str(len(body)).encode()),
                            (b"content-type", b"text/plain; charset=utf-8"),
                        ],
                    )
                )
            )
            writer.write(conn.send(h11.Data(data=body)))
            writer.write(conn.send(h11.EndOfMessage()))
        except Exception:
            pass
