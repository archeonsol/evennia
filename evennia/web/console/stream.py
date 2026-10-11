"""The server-sent-events endpoint carrying the live feed.

A plain async Django view rather than a DRF one. DRF builds a response and
returns it; this holds the connection open and writes to it for as long as
somebody is watching, which is a different shape. The capability check is
therefore done explicitly here rather than through ``ConsolePermission``, and
it must stay identical to it -- see :func:`_authorize`.

Streaming under ASGI wants an *async* generator. A sync one would occupy a
thread-pool worker for the whole life of the connection, so a handful of open
consoles would exhaust the pool that every other request needs. The producers
are synchronous, so each sweep hops to a worker thread for its own duration
only.

"""

from __future__ import annotations

import asyncio
import json
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse

from evennia.console.feed import KEEPALIVE_SECONDS, TICK_SECONDS, Stream, parse_topics
from evennia.web.console.auth import (
    CONSOLE_HEADER,
    ConsoleIdle,
    ConsoleInsecure,
    _meta_key,
    enforce_transport,
    live_capabilities,
    touch_idle,
)


def _authorize(request):
    """Return the console capabilities this request carries.

    Deliberately the same three conditions ``ConsolePermission`` applies:
    the console is enabled, the caller is authenticated, and the scoped header
    is present. Kept in step by hand because this view cannot use a DRF
    permission class; a divergence here would be a hole in the one place that
    stays open longest.

    An idle or insecure session is not an empty answer, it is a refusal with a
    reason the operator has to read, so those two propagate. They derive from
    DRF's ``PermissionDenied``, which is not Django's: an ``except`` for
    Django's never caught them, and a console tab left open past the idle bound
    answered every reconnect with an uncaught error and a 500.

    Args:
        request: The Django request.

    Returns:
        frozenset[str]: Held console capabilities, empty when refused.

    Raises:
        ConsoleInsecure: The request is not over TLS.
        ConsoleIdle: The session sat idle past the console's bound.
    """

    if not getattr(settings, "CONSOLE_ENABLED", True):
        return frozenset()
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return frozenset()
    if not request.META.get(_meta_key(CONSOLE_HEADER)):
        return frozenset()
    enforce_transport(request)
    touch_idle(request)
    return live_capabilities(user)


def _stream_check(request):
    """Return why the caller of an open stream may no longer have it, if so.

    Args:
        request: The request that opened the stream.

    Returns:
        dict | None: ``None`` while access stands. Otherwise ``reason`` for the
            operator and ``reauthenticate``, so the stream closes with a
            sentence the console can show instead of raising mid-flight.
    """

    try:
        if _authorize(request):
            return None
    except ConsoleIdle as error:
        return {"reason": str(error), "reauthenticate": True}
    except ConsoleInsecure as error:
        return {"reason": str(error), "reauthenticate": False}
    return {"reason": "Console access was revoked.", "reauthenticate": False}


def _refused(error):
    """Answer an idle or insecure refusal the way the REST views do.

    Args:
        error: The :class:`ConsoleIdle` or :class:`ConsoleInsecure` raised.

    Returns:
        JsonResponse: A 403 whose body carries the reason and, for an idle
            session, ``reauthenticate``, with the same retry headers the REST
            views send, so the client reads one shape from every endpoint.
    """

    response = JsonResponse(
        {
            "detail": str(error),
            "reauthenticate": isinstance(error, ConsoleIdle),
            "outcome": "conflict",
        },
        status=403,
    )
    response["X-Console-Retryable"] = "false"
    response["X-Console-Outcome"] = "conflict"
    return response


def _last_seen(request):
    """Return the sequence number a reconnecting client already has.

    ``EventSource`` sends it back as ``Last-Event-ID`` without being asked,
    which is what makes replay work with no client code.
    """

    raw = request.META.get("HTTP_LAST_EVENT_ID") or request.GET.get("since")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


#: Seconds between capability re-checks inside an open stream. Far below any
#: useful attack window, and one cached lookup each time.
RECHECK_SECONDS = 30.0


async def _events(stream, last_seq, recheck=None):
    """Yield SSE text for one connected console.

    Args:
        stream: The :class:`~evennia.console.feed.Stream` to draw from.
        last_seq: Sequence the client already received, or ``None``.
        recheck: Callable returning ``None`` while the caller still holds
            access, or a dict with ``reason`` and ``reauthenticate`` saying why
            not. A stream outlives the request that opened it, so a grant
            revoked mid-stream would otherwise keep flowing until the client
            chose to reconnect.

    Yields:
        str: Encoded SSE frames and keepalive comments.
    """

    yield ": open\n\n"
    for frame in stream.replay(last_seq):
        yield frame.encode()

    last_write = time.monotonic()
    last_check = time.monotonic()
    while True:
        if recheck is not None and time.monotonic() - last_check > RECHECK_SECONDS:
            last_check = time.monotonic()
            refusal = await sync_to_async(recheck, thread_sensitive=True)()
            if refusal:
                closed = {"t": "closed", **refusal}
                yield f"event: closed\ndata: {json.dumps(closed)}\n\n"
                return
        frames = await sync_to_async(stream.due, thread_sensitive=False)()
        if frames:
            yield "".join(frame.encode() for frame in frames)
            last_write = time.monotonic()
        elif time.monotonic() - last_write > KEEPALIVE_SECONDS:
            # A comment, not a frame: it keeps proxies from closing an idle
            # connection without spending a sequence number on nothing.
            yield ": keepalive\n\n"
            last_write = time.monotonic()
        await asyncio.sleep(TICK_SECONDS)


async def feed_view(request):
    """Stream the live feed to one authorized console.

    Args:
        request: The Django request.

    Returns:
        StreamingHttpResponse: The event stream.

    Raises:
        Http404: The console is switched off.
    """

    if not getattr(settings, "CONSOLE_ENABLED", True):
        raise Http404("The console is disabled.")

    try:
        capabilities = await sync_to_async(_authorize, thread_sensitive=True)(request)
    except (ConsoleIdle, ConsoleInsecure) as error:
        return _refused(error)
    if not capabilities:
        return HttpResponse(status=403, content=b"The console feed requires console access.")

    topics = parse_topics(request.GET.get("topics"))
    # The watch feed is scoped to the operator who started the watch, so the
    # stream has to know who is on the other end of it.
    stream = Stream(topics=topics, actor_id=getattr(getattr(request, "user", None), "pk", 0) or 0)
    response = StreamingHttpResponse(
        _events(stream, _last_seen(request), recheck=lambda: _stream_check(request)),
        content_type="text/event-stream",
    )
    # Buffering a stream defeats it; a proxy that ignores this will simply
    # deliver frames late rather than incorrectly.
    response["Cache-Control"] = "no-cache, no-store"
    response["X-Accel-Buffering"] = "no"
    response["X-Console-Topics"] = ",".join(topics)
    return response
