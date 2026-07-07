"""In-process Evennia bootstrap (headless mode, T3 S10).

Boot the engine without ``twistd`` or the ``evennia`` launcher CLI — useful for
tests, embedding, and REPL exploration::

    import evennia
    ctx = evennia.standalone(portal_mode=False, start_loop=True, background=True)
    # ... use engine API ...
    evennia.shutdown_standalone(ctx)
"""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from typing import Any

from evennia.utils import clock


@dataclass
class StandaloneContext:
    """Handles for a :func:`standalone` boot."""

    loop: asyncio.AbstractEventLoop
    application: Any
    portal_mode: bool
    thread: threading.Thread | None = None

    def stop(self):
        shutdown_standalone(self)


def standalone(
    *,
    portal_mode: bool = False,
    start_loop: bool = True,
    background: bool = False,
    settings_module: str | None = None,
) -> StandaloneContext:
    """Boot Evennia in-process.

    Args:
        portal_mode: ``True`` for Portal (listeners + redis bus), ``False`` for Server.
        start_loop: When ``True``, run the process asyncio loop after starting services.
        background: When ``True`` with ``start_loop``, run the loop on a daemon thread
            and return immediately. When ``False``, ``start_loop`` blocks the caller.
        settings_module: Optional Django settings dotted path (defaults to env).

    Returns:
        :class:`StandaloneContext` with ``loop``, ``application``, and ``portal_mode``.
    """
    if settings_module:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

    import django

    django.setup()

    import evennia

    evennia._init(portal_mode=portal_mode)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    clock.bind_loop(loop)

    application = evennia.TWISTED_APPLICATION
    application.startService()

    ctx = StandaloneContext(
        loop=loop,
        application=application,
        portal_mode=portal_mode,
    )

    if not start_loop:
        return ctx

    if background:
        thread = threading.Thread(target=loop.run_forever, name="evennia-standalone", daemon=True)
        thread.start()
        ctx.thread = thread
        return ctx

    try:
        loop.run_forever()
    finally:
        shutdown_standalone(ctx)
    return ctx


def shutdown_standalone(ctx: StandaloneContext | None = None):
    """Gracefully stop a :func:`standalone` boot."""
    if ctx is None:
        loop = clock.get_bound_loop()
        try:
            import evennia

            application = evennia.TWISTED_APPLICATION
        except Exception:
            application = None
    else:
        loop = ctx.loop
        application = ctx.application

    clock.run_shutdown_hooks()
    if application is not None:
        try:
            if application.running:
                application.stopService()
        except Exception:
            pass

    if loop is not None and loop.is_running():
        clock.stop_loop()

    if ctx is not None and ctx.thread is not None and ctx.thread.is_alive():
        ctx.thread.join(timeout=5)

    if loop is not None and not loop.is_closed():
        try:
            loop.close()
        except Exception:
            pass
