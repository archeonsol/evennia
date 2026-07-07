"""Engine timer/loop facade: the single chokepoint for event-loop scheduling.

Every scheduled call, repeating loop, and worker-thread hop in the engine flows
through here instead of importing ``twisted.internet.reactor`` directly.
Implementations use native asyncio primitives on the process-owned loop (T3 S9).
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from concurrent.futures import ThreadPoolExecutor

# Twisted default reactor threadpool uses maxthreads=10.
_DEFAULT_EXECUTOR_WORKERS = 10

_pending_when_running: list[tuple] = []
_shutdown_hooks: list[tuple] = []
_default_executor: ThreadPoolExecutor | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the process-owned loop (asyncio bootstrap, T3 S9)."""
    global _main_loop
    _main_loop = loop


def get_bound_loop() -> asyncio.AbstractEventLoop | None:
    """Return the bound process loop, or None if bootstrap has not run yet."""
    if _main_loop is not None and not _main_loop.is_closed():
        return _main_loop
    return None


def loop_running() -> bool:
    loop = get_bound_loop()
    return loop is not None and loop.is_running()


def register_shutdown_hook(fn, *args, **kwargs):
    """Register a callable to run during graceful process shutdown."""
    _shutdown_hooks.append((fn, args, kwargs))


def run_shutdown_hooks():
    """Fire shutdown hooks registered via :func:`register_shutdown_hook`."""
    hooks = _shutdown_hooks[:]
    _shutdown_hooks.clear()
    for fn, args, kwargs in hooks:
        try:
            fn(*args, **kwargs)
        except Exception:
            from evennia.utils.logger import log_trace

            log_trace("shutdown hook failed")


def stop_loop():
    """Stop the running process loop (replaces ``reactor.stop()`` on the hot path)."""
    loop = get_bound_loop()
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
        return
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
            return
    except RuntimeError:
        pass
    # Legacy twistd dev fallback (Windows).
    try:
        from twisted.internet import reactor

        if reactor.running:
            reactor.stop()
    except Exception:
        pass


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return the running asyncio loop, or the bound bootstrap loop."""
    global _main_loop
    try:
        loop = asyncio.get_running_loop()
        _main_loop = loop
        return loop
    except RuntimeError:
        pass
    if _main_loop is not None and not _main_loop.is_closed():
        return _main_loop
    try:
        loop = asyncio.get_event_loop()
        if loop is not None and not loop.is_closed():
            return loop
    except RuntimeError:
        pass
    # Last-resort dev fallback when twistd still owns the bridge.
    try:
        from twisted.internet import reactor

        loop = getattr(reactor, "_asyncioEventloop", None)
        if loop is not None and not loop.is_closed():
            return loop
    except Exception:
        pass
    raise RuntimeError("no running event loop")


def _flush_when_running(loop: asyncio.AbstractEventLoop) -> None:
    if not _pending_when_running:
        return
    pending = _pending_when_running[:]
    _pending_when_running.clear()
    for fn, args, kwargs in pending:
        loop.call_soon(fn, *args, **kwargs)


def _get_default_executor() -> ThreadPoolExecutor:
    global _default_executor
    if _default_executor is None:
        _default_executor = ThreadPoolExecutor(
            max_workers=_DEFAULT_EXECUTOR_WORKERS,
            thread_name_prefix="evennia-io",
        )
    return _default_executor


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        from evennia.utils.logger import log_err

        log_err(f"Unhandled error in scheduled coroutine:\n{exc}")


class LoopHandle:
    """Asyncio-backed repeating call with a LoopingCall-compatible surface."""

    def __init__(self, fn, *args, on_error=None, **kwargs):
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._on_error = on_error
        self._interval = None
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self.running = False

    def start(self, interval, now=False):
        if self.running:
            return self._stopped
        self._interval = interval
        self.running = True
        self._stopped.clear()
        loop = _get_loop()
        _flush_when_running(loop)
        self._task = loop.create_task(self._loop(now))
        return self._stopped

    def stop(self):
        self.running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._stopped.set()

    async def _loop(self, fire_immediately: bool):
        try:
            if fire_immediately:
                await self._run_once()
            while self.running:
                await asyncio.sleep(self._interval)
                if not self.running:
                    break
                await self._run_once()
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            self._stopped.set()

    async def _run_once(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
            await maybe_await(result)
        except Exception:
            if self._on_error is not None:
                try:
                    from twisted.python.failure import Failure

                    self._on_error(Failure())
                except Exception:
                    self._on_error(None)
            self.running = False
            if self._task is not None and not self._task.done():
                self._task.cancel()


class _SyncCoroutineResult:
    """Test-harness result when ``run_coroutine`` is called with no running loop."""

    def __init__(self, coro):
        loop = asyncio.new_event_loop()
        try:
            self._result = loop.run_until_complete(coro)
            self._exception = None
        except Exception as exc:
            self._result = None
            self._exception = exc
            _log_task_exception_from_exception(exc)
        finally:
            loop.close()

    def add_done_callback(self, callback):
        callback(self)

    def exception(self):
        return self._exception

    def cancelled(self):
        return False

    def result(self):
        if self._exception is not None:
            raise self._exception
        return self._result


def _log_task_exception_from_exception(exc: BaseException) -> None:
    from evennia.utils.logger import log_err

    log_err(f"Unhandled error in scheduled coroutine:\n{exc}")


def call_later(seconds, fn, *args, **kwargs):
    """Schedule ``fn(*args, **kwargs)`` once, ``seconds`` from now.

    Returns:
        A cancellable handle (``asyncio.TimerHandle``: ``.cancel()``).
    """
    loop = _get_loop()
    _flush_when_running(loop)
    if kwargs:
        fn = functools.partial(fn, *args, **kwargs)
        return loop.call_later(seconds, fn)
    return loop.call_later(seconds, fn, *args)


def call_from_thread(fn, *args, **kwargs):
    """Run ``fn`` on the reactor/loop thread from a worker thread (thread-safe)."""
    loop = _get_loop()
    if kwargs:
        fn = functools.partial(fn, *args, **kwargs)
        return loop.call_soon_threadsafe(fn)
    return loop.call_soon_threadsafe(fn, *args, **kwargs)


def when_running(fn, *args, **kwargs):
    """Run ``fn`` once the reactor/loop is running (or immediately if it is)."""
    try:
        loop = _get_loop()
    except RuntimeError:
        _pending_when_running.append((fn, args, kwargs))
        return None
    _flush_when_running(loop)
    return loop.call_soon(fn, *args, **kwargs)


async def maybe_await(result):
    """Await ``result`` if it is awaitable, else return it unchanged.

    The async equivalent of Twisted ``inlineCallbacks``' lenient ``yield``: that
    idiom waits on a Deferred but passes a plain value straight through. When a
    generator that yields a *mix* of Deferreds and plain values (as the cmdset
    merge + command dispatch code does) is converted to ``async``/``await``,
    replace ``x = yield expr`` with ``x = await maybe_await(expr)`` so both cases
    keep working without per-yield analysis.
    """
    if inspect.isawaitable(result):
        return await result
    return result


def run_coroutine(coro):
    """Schedule an ``async`` coroutine to run on the event loop.

    Returns an ``asyncio.Task`` when a loop is running. When called from a test
    harness with no running loop, drives the coroutine synchronously and returns
    a lightweight result object.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _SyncCoroutineResult(coro)
    task = loop.create_task(coro)
    task.add_done_callback(_log_task_exception)
    return task


def make_looping(fn, *args, **kwargs):
    """Build a repeating-call handle WITHOUT starting it."""
    return LoopHandle(fn, *args, **kwargs)


def looping(interval, fn, *args, now=False, on_error=None, **kwargs):
    """Start a repeating call of ``fn(*args, **kwargs)`` every ``interval`` seconds."""
    handle = LoopHandle(fn, *args, on_error=on_error, **kwargs)
    handle.start(interval, now=now)
    return handle


def defer_to_thread(fn, *args, **kwargs):
    """Run a blocking callable in the default thread pool; return an awaitable Future."""
    loop = _get_loop()
    _flush_when_running(loop)
    if kwargs:
        fn = functools.partial(fn, *args, **kwargs)
        return loop.run_in_executor(_get_default_executor(), fn)
    return loop.run_in_executor(_get_default_executor(), fn, *args)


def defer_later(seconds, fn=None, *args, **kwargs):
    """Awaitable delay (used by pause/generator drivers).

    With no ``fn`` this is a plain ``seconds``-long sleep whose result is
    ``None``. With ``fn`` the callable runs after the delay and its return
    value is the awaitable's result.
    """

    async def _delayed():
        await asyncio.sleep(seconds)
        if fn is None:
            return None
        if kwargs:
            return fn(*args, **kwargs)
        return fn(*args)

    loop = _get_loop()
    return loop.create_task(_delayed())


class _DeferLaterCompat:
    """Twisted ``deferLater``-compatible delayed call (``.called``, ``.cancel()``, pause)."""

    def __init__(self, seconds, fn, *args, **kwargs):
        self.called = False
        self.paused = False
        self._cancelled = False
        self._pending_fire = False
        self._errbacks = []
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._handle = call_later(seconds, self._fire)

    def _fire(self):
        if self._cancelled:
            return
        if self.paused:
            self._pending_fire = True
            return
        self._invoke()

    def _invoke(self):
        self.called = True
        try:
            if self._kwargs:
                self._fn(*self._args, **self._kwargs)
            else:
                self._fn(*self._args)
        except Exception as exc:
            self._run_errbacks(exc)

    def _run_errbacks(self, exc):
        for errfn, args, kwargs in self._errbacks:
            try:
                from twisted.python.failure import Failure

                result = errfn(Failure(exc), *args, **kwargs)
            except Exception:
                try:
                    errfn(exc, *args, **kwargs)
                except Exception:
                    pass
            if hasattr(result, "raiseException"):
                result.raiseException()

    def cancel(self):
        if self.called or self._cancelled:
            return
        self._cancelled = True
        if self._handle is not None:
            self._handle.cancel()

    def pause(self):
        self.paused = True

    def unpause(self):
        self.paused = False
        if self._pending_fire:
            self._pending_fire = False
            self._invoke()

    def addErrback(self, fn, *args, **kwargs):
        self._errbacks.append((fn, args, kwargs))
        return self


def defer_later_compat(clock, seconds, fn, *args, **kwargs):
    """Schedule ``fn`` after ``seconds`` with a Twisted-Deferred-compatible handle.

    When ``clock`` is a Twisted test :class:`~twisted.internet.task.Clock`, delegates
    to Twisted ``deferLater`` so existing unit tests keep working.
    """
    try:
        from twisted.internet.task import Clock as TwistedClock
        from twisted.internet.task import deferLater

        if isinstance(clock, TwistedClock):
            return deferLater(clock, seconds, fn, *args, **kwargs)
    except Exception:
        pass
    return _DeferLaterCompat(seconds, fn, *args, **kwargs)
