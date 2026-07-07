"""Outbound HTTP client facade (replaces ``twisted.web.client.Agent``).

Returns a Twisted ``Deferred`` so existing reactor callers are unchanged, but the
request itself runs on ``httpx`` in the reactor thread pool (via
``clock.defer_to_thread``). That works on any reactor and is dev-testable, unlike
a reactor-native async client which would need the asyncio reactor. When the
runtime goes pure-asyncio (T3 end), swap the body here to ``httpx.AsyncClient``
awaited on the loop; call sites stay put.

The ``Response`` exposes ``.code`` (the name Twisted's Agent response used) plus
``.status_code``/``.content``/``.json()`` so callers read either style.

These are low-frequency outbound calls (Discord REST, game-index check-ins, LLM
requests); a thread per request is a non-issue and httpx keeps its own pool.
"""

import json as _json

import httpx

from evennia.utils import clock


class Response:
    """Minimal HTTP response (Agent-compatible ``.code``)."""

    def __init__(self, status_code, content, headers=None):
        self.code = status_code            # twisted.web Agent response attribute
        self.status_code = status_code
        self.content = content             # bytes
        self.headers = headers or {}

    def json(self):
        return _json.loads(self.content)

    def __repr__(self):
        return f"<http.Response {self.status_code} ({len(self.content)}b)>"


def _flatten_headers(headers):
    """Accept Twisted-style ``{name: [values]}`` or plain ``{name: value}``."""
    if not headers:
        return None
    out = {}
    for name, value in headers.items():
        out[name] = value[0] if isinstance(value, (list, tuple)) else value
    return out


def request(method, url, headers=None, data=None, timeout=30):
    """Make an HTTP request off the reactor thread; return a ``Deferred``.

    Args:
        method (str): "GET"/"POST"/"PATCH"/...
        url (str): Target URL.
        headers (dict, optional): ``{name: value}`` or ``{name: [value]}``.
        data (bytes or str, optional): Request body (already-encoded).
        timeout (float): Seconds.

    Returns:
        Deferred: fires with a :class:`Response` on the reactor thread.
    """
    flat = _flatten_headers(headers)

    def _do():
        resp = httpx.request(method, url, headers=flat, content=data, timeout=timeout)
        return Response(resp.status_code, resp.content, dict(resp.headers))

    return clock.defer_to_thread(_do)


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)
