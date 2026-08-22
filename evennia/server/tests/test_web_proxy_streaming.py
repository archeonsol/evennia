"""Tests for streaming responses through the asyncio Portal web proxy."""

import asyncio
from unittest import IsolatedAsyncioTestCase

import h11

from evennia.server.portal.web_proxy import ReverseProxy


class _Writer:
    """Collect bytes written by the proxy."""

    def __init__(self):
        self.writes = []

    def write(self, data):
        """Keep one wire fragment."""

        self.writes.append(data)

    async def drain(self):
        """Model an immediately writable client socket."""


class _StreamingResponse:
    """Yield one SSE frame, then remain open like a live feed."""

    status_code = 200
    headers = type(
        "Headers",
        (),
        {"raw": [(b"content-type", b"text/event-stream"), (b"x-accel-buffering", b"no")]},
    )()

    def __init__(self):
        self.first_sent = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def content(self):
        """Fail if the proxy tries to buffer the never-ending body."""

        raise AssertionError("a streaming proxy must not read response.content")

    async def aiter_raw(self):
        """Produce the first frame without ending the response."""

        yield b": open\n\n"
        self.first_sent.set()
        await self.release.wait()


class _StreamContext:
    """Async context manager returned by ``httpx.AsyncClient.stream``."""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Client:
    """Return one controlled upstream stream."""

    def __init__(self, response):
        self.response = response

    def stream(self, *args, **kwargs):
        """Open the controlled response."""

        return _StreamContext(self.response)


class TestReverseProxyStreaming(IsolatedAsyncioTestCase):
    """The public proxy must not wait for an SSE response to finish."""

    async def test_forwards_a_frame_while_the_upstream_is_still_open(self):
        proxy = ReverseProxy("127.0.0.1", 4005)
        upstream = _StreamingResponse()
        proxy._client = _Client(upstream)
        writer = _Writer()
        conn = h11.Connection(h11.SERVER)
        conn.receive_data(b"GET /api/console/feed/ HTTP/1.1\r\nHost: game\r\n\r\n")
        request = conn.next_event()
        self.assertIsInstance(conn.next_event(), h11.EndOfMessage)

        forwarding = asyncio.create_task(
            proxy._forward_and_respond(conn, writer, request, b"", "198.51.100.4")
        )
        try:
            await asyncio.wait_for(upstream.first_sent.wait(), timeout=1)
            await asyncio.sleep(0)

            self.assertFalse(forwarding.done())
            wire = b"".join(writer.writes)
            self.assertIn(b"HTTP/1.1 200", wire)
            self.assertIn(b"text/event-stream", wire)
            self.assertIn(b": open\n\n", wire)
        finally:
            upstream.release.set()
            await forwarding
