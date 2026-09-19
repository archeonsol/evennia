"""Process event-loop factory.

The Portal/Server bootstrap and the ASGI web thread each build their own asyncio
loop. Centralize the concrete loop choice here so the whole engine shares one
policy: prefer uvloop when it is installed, fall back cleanly to the stdlib loop
on platforms without uvloop wheels (Windows dev).
"""

from __future__ import annotations

import asyncio


def new_process_loop():
    """Create a new event loop, preferring uvloop when it is importable.

    uvloop is an optional, drop-in asyncio replacement with Linux/macOS wheels
    only. A missing or unsupported install falls back to the stdlib loop, so
    callers never need their own platform check.

    Returns:
        asyncio.AbstractEventLoop: A fresh, unclosed event loop.

    """
    try:
        import uvloop
    except ImportError:
        return asyncio.new_event_loop()
    return uvloop.new_event_loop()


def loop_name(loop) -> str:
    """Return a short implementation label for logging.

    Args:
        loop (asyncio.AbstractEventLoop): A loop built by :func:`new_process_loop`.

    Returns:
        str: ``"uvloop"`` for a uvloop loop, ``"asyncio"`` otherwise.

    """
    return "uvloop" if type(loop).__module__.split(".")[0] == "uvloop" else "asyncio"
