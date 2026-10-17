"""The Portal web proxy meets a Server that is restarting, and a browser that leaves."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import h11
import httpx

from evennia.server.portal import web_proxy
from evennia.server.portal.web_proxy import ReverseProxy


class _Writer:
    """Collect what the proxy writes, and model a socket that can close."""

    def __init__(self):
        self.writes = []
        self.closing = False

    def is_closing(self):
        """Report whether the browser's socket has closed."""

        return self.closing

    def write(self, data):
        """Keep one wire fragment; a closed uvloop socket raises RuntimeError."""

        if self.closing:
            raise RuntimeError("unable to perform operation on <TCPTransport closed=True>")
        self.writes.append(data)

    async def drain(self):
        """Model an immediately writable client socket."""

    def wire(self):
        """Everything written, lower case, for header checks."""

        return b"".join(self.writes).lower()


class _Stream:
    """Async context manager that fails on entry, or hands out a response."""

    def __init__(self, error=None, response=None):
        self.error = error
        self.response = response

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Client:
    """An upstream that answers every request the same way."""

    def __init__(self, error=None, response=None):
        self.error = error
        self.response = response

    def stream(self, *args, **kwargs):
        """Open the controlled stream."""

        return _Stream(self.error, self.response)


class _Response:
    """An upstream response that gives its chunks, then optionally fails."""

    status_code = 200
    headers = type("Headers", (), {"raw": [(b"content-type", b"text/plain")]})()

    def __init__(self, chunks, then_raise=None, writer=None):
        self.chunks = chunks
        self.then_raise = then_raise
        self.writer = writer

    async def aiter_raw(self):
        """Yield the chunks; the browser leaves after the first if a writer is set."""

        for index, chunk in enumerate(self.chunks):
            yield chunk
            if index == 0 and self.writer is not None:
                self.writer.closing = True
        if self.then_raise is not None:
            raise self.then_raise


def _request(target=b"/api/thing?token=secret"):
    conn = h11.Connection(h11.SERVER)
    conn.receive_data(b"GET " + target + b" HTTP/1.1\r\nHost: game\r\n\r\n")
    request = conn.next_event()
    assert isinstance(conn.next_event(), h11.EndOfMessage)
    return conn, request


class TestUpstreamUnavailable(IsolatedAsyncioTestCase):
    """A request that arrives while the Server is down is expected, not a fault."""

    async def _forward(self, client, writer=None, proxy=None):
        proxy = proxy or ReverseProxy("127.0.0.1", 4005)
        proxy._client = client
        writer = writer or _Writer()
        conn, request = _request()
        with (
            patch.object(web_proxy.logger, "log_trace") as trace,
            patch.object(web_proxy.logger, "log_warn") as warn,
        ):
            forwarded = await proxy._forward_and_respond(conn, writer, request, b"", None)
        return forwarded, writer, trace, warn

    async def test_a_refused_connection_answers_503_and_logs_one_line_without_a_traceback(self):
        error = httpx.ConnectError("All connection attempts failed")

        forwarded, writer, trace, warn = await self._forward(_Client(error=error))

        self.assertFalse(forwarded)
        self.assertIn(b"http/1.1 503", writer.wire())
        self.assertIn(b"retry-after: 5", writer.wire())
        trace.assert_not_called()
        warn.assert_called_once()
        line = warn.call_args.args[0]
        self.assertIn("ConnectError", line)
        self.assertIn("GET /api/thing", line)
        self.assertNotIn("secret", line)

    async def test_a_server_that_hangs_up_answers_502_with_retry_after(self):
        error = httpx.RemoteProtocolError("Server disconnected without sending a response.")

        forwarded, writer, trace, warn = await self._forward(_Client(error=error))

        self.assertFalse(forwarded)
        self.assertIn(b"http/1.1 502", writer.wire())
        self.assertIn(b"retry-after: 5", writer.wire())
        trace.assert_not_called()
        warn.assert_called_once()

    async def test_a_read_error_answers_502(self):
        _forwarded, writer, trace, _warn = await self._forward(
            _Client(error=httpx.ReadError("connection reset"))
        )

        self.assertIn(b"http/1.1 502", writer.wire())
        trace.assert_not_called()

    async def test_a_timeout_answers_504(self):
        _forwarded, writer, trace, warn = await self._forward(
            _Client(error=httpx.ReadTimeout("timed out"))
        )

        self.assertIn(b"http/1.1 504", writer.wire())
        trace.assert_not_called()
        self.assertIn("ReadTimeout", warn.call_args.args[0])

    async def test_a_failure_that_is_not_the_server_going_away_keeps_its_traceback(self):
        forwarded, writer, trace, warn = await self._forward(_Client(error=ValueError("a bug")))

        self.assertFalse(forwarded)
        self.assertIn(b"http/1.1 502", writer.wire())
        self.assertNotIn(b"retry-after", writer.wire())
        trace.assert_called_once()
        warn.assert_not_called()

    async def test_a_failure_after_the_response_began_sends_no_second_page(self):
        response = _Response([b"part one"], then_raise=httpx.ReadError("peer closed"))

        forwarded, writer, trace, warn = await self._forward(_Client(response=response))

        self.assertFalse(forwarded)
        wire = writer.wire()
        self.assertIn(b"http/1.1 200", wire)
        self.assertNotIn(b"http/1.1 502", wire)
        trace.assert_not_called()
        self.assertIn("after the response had begun", warn.call_args.args[0])

    async def test_a_burst_of_failures_is_logged_once_and_counted(self):
        proxy = ReverseProxy("127.0.0.1", 4005)
        now = [100.0]
        proxy._now = lambda: now[0]
        client = _Client(error=httpx.ConnectError("refused"))
        lines = []
        for moment in (100.0, 101.0, 105.0, 111.0):
            now[0] = moment
            _forwarded, _writer, _trace, warn = await self._forward(client, proxy=proxy)
            lines.extend(call.args[0] for call in warn.call_args_list)

        self.assertEqual(len(lines), 2)
        self.assertNotIn("more since", lines[0])
        self.assertIn("(2 more since the last line)", lines[1])


class TestClientLeaves(IsolatedAsyncioTestCase):
    """A browser that closes its tab mid-response is a normal end, not an error."""

    async def _forward(self, response, writer):
        proxy = ReverseProxy("127.0.0.1", 4005)
        proxy._client = _Client(response=response)
        conn, request = _request()
        with (
            patch.object(web_proxy.logger, "log_trace") as trace,
            patch.object(web_proxy.logger, "log_warn") as warn,
        ):
            forwarded = await proxy._forward_and_respond(conn, writer, request, b"", None)
        return forwarded, trace, warn

    async def test_a_socket_that_closed_before_the_headers_ends_the_exchange_quietly(self):
        writer = _Writer()
        writer.closing = True

        forwarded, trace, warn = await self._forward(_Response([b"body"]), writer)

        self.assertFalse(forwarded)
        self.assertEqual(writer.writes, [])
        trace.assert_not_called()
        warn.assert_not_called()

    async def test_a_socket_that_closes_mid_stream_ends_the_exchange_quietly(self):
        writer = _Writer()
        response = _Response([b"one", b"two", b"three"], writer=writer)

        forwarded, trace, warn = await self._forward(response, writer)

        self.assertFalse(forwarded)
        self.assertIn(b"one", b"".join(writer.writes))
        self.assertNotIn(b"two", b"".join(writer.writes))
        trace.assert_not_called()
        warn.assert_not_called()
