"""Engine timer/loop facade: the single chokepoint for reactor scheduling.

Every scheduled call, repeating loop, and worker-thread hop in the engine is
meant to flow through here instead of importing ``twisted.internet.reactor`` /
``twisted.internet.task`` directly. Today each function delegates straight to
the Twisted reactor (which itself runs on the asyncio event loop under
``TWISTED_REACTOR=asyncio``). When Twisted is dropped (T3), only the bodies in
this module change, to ``loop.call_later`` / ``loop.call_soon_threadsafe`` /
native asyncio timers, and the call sites stay exactly as they are.

Companion to ``evennia.utils.defer`` (the blocking-work-off-the-loop chokepoint)
and ``evennia.utils.utils.delay`` (the persistent, game-facing timer built on
the task handler). Those already funnel their Twisted use through one place; this
module does the same for the bare scheduling primitives.

Handles are returned as-is for now (Twisted ``DelayedCall`` / ``LoopingCall``),
so existing ``.cancel()`` / ``.active()`` / ``.stop()`` / ``.running`` usage keeps
working; the asyncio flip will preserve that surface behind a uniform handle.
"""

from twisted.internet import reactor, task, threads


def call_later(seconds, fn, *args, **kwargs):
    """Schedule ``fn(*args, **kwargs)`` once, ``seconds`` from now.

    Returns:
        A cancellable handle (Twisted ``DelayedCall``: ``.cancel()``/``.active()``).
    """
    return reactor.callLater(seconds, fn, *args, **kwargs)


def call_from_thread(fn, *args, **kwargs):
    """Run ``fn`` on the reactor/loop thread from a worker thread (thread-safe)."""
    return reactor.callFromThread(fn, *args, **kwargs)


def when_running(fn, *args, **kwargs):
    """Run ``fn`` once the reactor/loop is running (or immediately if it is)."""
    return reactor.callWhenRunning(fn, *args, **kwargs)


def run_coroutine(coro):
    """Schedule an ``async`` coroutine to run on the reactor/loop.

    The bridge for the Deferred->async migration: an ``async def`` returns a
    coroutine that nobody runs on its own, so callers that kick one off (outside
    an ``await``) route it through here. Today it is wrapped as a Twisted
    ``Deferred`` (so ``.addErrback``/``.addCallback`` still work during the
    transition); at pure-asyncio this becomes ``loop.create_task``.
    """
    from twisted.internet.defer import ensureDeferred

    return ensureDeferred(coro)


def make_looping(fn, *args, **kwargs):
    """Build a repeating-call handle WITHOUT starting it.

    For the delayed-start case (start the loop from a later ``call_later``). The
    caller drives ``.start(interval, now=...)`` / ``.stop()`` on the handle.
    """
    return task.LoopingCall(fn, *args, **kwargs)


def looping(interval, fn, *args, now=False, on_error=None, **kwargs):
    """Start a repeating call of ``fn(*args, **kwargs)`` every ``interval`` seconds.

    Args:
        on_error (callable, optional): Errback attached to the loop's Deferred,
            called if a run raises (the loop otherwise stops silently on error).

    Returns:
        The started loop handle (Twisted ``LoopingCall``: ``.stop()``/``.running``).
    """
    loop = task.LoopingCall(fn, *args, **kwargs)
    deferred = loop.start(interval, now=now)
    if on_error is not None:
        deferred.addErrback(on_error)
    return loop


def defer_to_thread(fn, *args, **kwargs):
    """Run a blocking callable in the reactor thread pool; return a Deferred.

    The raw thread-offload chokepoint. Prefer ``evennia.utils.defer.in_thread``
    for game code (it adds Django connection hygiene + the game-state-safety
    contract); this bare version is for engine internals that only do I/O.
    """
    return threads.deferToThread(fn, *args, **kwargs)


def defer_later(seconds, fn=None, *args, **kwargs):
    """Deferred-returning delay (used by pause/generator drivers).

    With no ``fn`` this is a plain ``seconds``-long sleep whose Deferred fires
    with ``None``.
    """
    return task.deferLater(reactor, seconds, fn or (lambda: None), *args, **kwargs)
