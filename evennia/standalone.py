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
from dataclasses import dataclass, field
from typing import Any

from evennia.utils import clock


@dataclass
class StandaloneContext:
    """Handles for a :func:`standalone` boot."""

    loop: asyncio.AbstractEventLoop
    application: Any
    portal_mode: bool
    thread: threading.Thread | None = None
    _ready: threading.Event = field(default_factory=threading.Event, repr=False)
    _finished: threading.Event = field(default_factory=threading.Event, repr=False)
    _shutdown_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _shutdown_started: bool = field(default=False, repr=False)
    _startup_error: BaseException | None = field(default=None, repr=False)

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
    application = evennia.TWISTED_APPLICATION
    ctx = StandaloneContext(
        loop=loop,
        application=application,
        portal_mode=portal_mode,
    )

    if background and start_loop:

        def run_background():
            try:
                asyncio.set_event_loop(loop)
                clock.bind_loop(loop)
                application.startService()
                loop.call_soon(ctx._ready.set)
                loop.run_forever()
            except BaseException as err:
                ctx._startup_error = err
                ctx._ready.set()
            finally:
                if not ctx._shutdown_started:
                    _finalize_stopped_standalone(ctx)
                if not loop.is_closed():
                    loop.close()
                ctx._finished.set()

        thread = threading.Thread(
            target=run_background,
            name="evennia-standalone",
            daemon=True,
        )
        ctx.thread = thread
        thread.start()
        ctx._ready.wait()
        if ctx._startup_error is not None:
            ctx._finished.wait(timeout=5)
            raise ctx._startup_error
        return ctx

    asyncio.set_event_loop(loop)
    clock.bind_loop(loop)
    try:
        application.startService()
    except BaseException:
        _finalize_stopped_standalone(ctx)
        loop.close()
        raise

    if not start_loop:
        return ctx

    try:
        loop.run_forever()
    finally:
        shutdown_standalone(ctx)
    return ctx


def _stop_application(application):
    """Stop a fully or partially started application without masking teardown."""
    if application is None:
        return
    try:
        application.stopService()
    except Exception:
        pass


def _finalize_stopped_standalone(ctx):
    """Finalize a stopped loop from its recorded owner thread."""
    try:
        clock.run_shutdown_hooks(shutdown_executor=False)
    except Exception:
        pass
    _stop_application(ctx.application)
    try:
        clock.cancel_pending_tasks(ctx.loop)
    except Exception:
        pass
    clock.shutdown_default_executor()
    ctx._shutdown_started = True


async def _finalize_running_standalone(ctx):
    """Settle a background standalone from inside its owner loop."""
    try:
        clock.run_shutdown_hooks(shutdown_executor=False)
    except Exception:
        pass
    _stop_application(ctx.application)
    current = asyncio.current_task()
    tasks = [
        task for task in asyncio.all_tasks(ctx.loop) if task is not current and not task.done()
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    clock.shutdown_default_executor()


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

    if ctx is None:
        ctx = StandaloneContext(loop=loop, application=application, portal_mode=False)

    with ctx._shutdown_lock:
        if ctx._shutdown_started:
            return
        ctx._shutdown_started = True

    if loop is not None and loop.is_running():

        def schedule_shutdown():
            task = loop.create_task(_finalize_running_standalone(ctx))

            def completed(done_task):
                try:
                    done_task.result()
                except BaseException:
                    pass
                loop.stop()

            task.add_done_callback(completed)

        loop.call_soon_threadsafe(schedule_shutdown)
        if ctx.thread is not None and threading.current_thread() is not ctx.thread:
            ctx._finished.wait(timeout=5)
            ctx.thread.join(timeout=5)
        return

    _finalize_stopped_standalone(ctx)
    if loop is not None and not loop.is_closed():
        loop.close()
