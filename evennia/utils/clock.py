"""Engine timer/loop facade: the single chokepoint for event-loop scheduling.

Every scheduled call, repeating loop, and worker-thread hop in the engine flows
through here instead of importing ``twisted.internet.reactor`` directly.
Implementations use native asyncio primitives on the process-owned loop (T3 S9).
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor

# Twisted default reactor threadpool uses maxthreads=10.
_DEFAULT_EXECUTOR_WORKERS = 10

_pending_when_running: list[tuple] = []
_pending_lock = threading.Lock()
_shutdown_hooks: list[tuple] = []
_default_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_main_loop: asyncio.AbstractEventLoop | None = None
_loop_thread_id: int | None = None
_runtime_task_kind: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "evennia_runtime_task_kind", default=None
)
_sync_inline_driver: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "evennia_sync_inline_driver", default=False
)
_sync_driver_loop: contextvars.ContextVar[asyncio.AbstractEventLoop | None] = (
    contextvars.ContextVar("evennia_sync_driver_loop", default=None)
)

_RUNTIME_TASK_KINDS = frozenset(
    {
        "action",
        "activity",
        "command",
        "job",
        "service",
        "system",
        "warmup",
        "generic",
        "test",
    }
)


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the process-owned loop (asyncio bootstrap, T3 S9).

    This is the sole writer of ``_main_loop``: the running loop is discovered via
    ``asyncio.get_running_loop()`` on the hot path, never rebound here-behind-the-back.
    """
    global _main_loop, _loop_thread_id
    _main_loop = loop
    # Record the owning thread so is_io_thread never reads the CPython-private
    # ``loop._thread_id``. bind_loop runs on the thread that then runs the loop.
    _loop_thread_id = threading.get_ident()
    # Build the worker pool here, in the single-threaded bootstrap context, so
    # concurrent defer_to_thread callers never race to construct duplicate pools.
    # (The pool spawns no threads until work is actually submitted.)
    _ensure_default_executor()


def get_bound_loop() -> asyncio.AbstractEventLoop | None:
    """Return the bound process loop, or None if bootstrap has not run yet."""
    if _main_loop is not None and not _main_loop.is_closed():
        return _main_loop
    return None


def loop_running() -> bool:
    loop = get_bound_loop()
    return loop is not None and loop.is_running()


def is_io_thread() -> bool:
    """True when the current thread is running the bound process event loop."""
    loop = get_bound_loop()
    if loop is None:
        return False
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        pass
    if _loop_thread_id is None:
        return False
    return threading.get_ident() == _loop_thread_id


def is_io_owner() -> bool:
    """Return whether this thread owns live Evennia runtime state.

    Before bootstrap, the actual main thread is the only supported inline
    owner. Once a loop is bound, its recorded thread is authoritative even
    while the loop is stopped for owner-side teardown.
    """
    if get_bound_loop() is None:
        return threading.current_thread() is threading.main_thread()
    return is_io_thread()


def get_loop_thread_id() -> int | None:
    """Return the thread id running the bound event loop, or None."""
    return _loop_thread_id


def register_shutdown_hook(fn, *args, **kwargs):
    """Register a callable to run during graceful process shutdown."""
    _shutdown_hooks.append((fn, args, kwargs))


def run_shutdown_hooks(*, shutdown_executor=True):
    """Fire shutdown hooks registered via :func:`register_shutdown_hook`."""
    hooks = _shutdown_hooks[:]
    _shutdown_hooks.clear()
    for fn, args, kwargs in hooks:
        try:
            fn(*args, **kwargs)
        except Exception:
            from evennia.utils.logger import log_trace

            log_trace("shutdown hook failed")
    # Tear the worker pool down last, after user hooks: a hook may still hand
    # blocking work to defer_to_thread, and its non-daemon threads would
    # otherwise outlive loop teardown and pile up across in-process reloads.
    if shutdown_executor:
        shutdown_default_executor()


def cancel_pending_tasks(loop, *, exclude=()):
    """Cancel and settle every unfinished task owned by ``loop`` once."""
    excluded = set(exclude)
    tasks = [
        task
        for task in asyncio.all_tasks(loop)
        if not task.done() and task not in excluded
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    return tasks


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


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return the running asyncio loop, or the bound bootstrap loop."""
    try:
        # Discover the running loop; do not rebind _main_loop (bind_loop owns it).
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    sync_driver_loop = _sync_driver_loop.get()
    if sync_driver_loop is not None and not sync_driver_loop.is_closed():
        return sync_driver_loop
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
    with _pending_lock:
        if not _pending_when_running:
            return
        pending = _pending_when_running[:]
        _pending_when_running.clear()
    for fn, args, kwargs in pending:
        loop.call_soon(
            _run_runtime_callback,
            fn,
            args,
            kwargs,
            "generic",
            context=_isolated_database_context(),
        )


def _ensure_default_executor() -> ThreadPoolExecutor:
    """Return the shared worker pool, building it once under a lock."""
    global _default_executor
    with _executor_lock:
        if _default_executor is None:
            _default_executor = ThreadPoolExecutor(
                max_workers=_DEFAULT_EXECUTOR_WORKERS,
                thread_name_prefix="evennia-io",
            )
        return _default_executor


def _get_default_executor() -> ThreadPoolExecutor:
    return _ensure_default_executor()


def shutdown_default_executor(wait: bool = False) -> None:
    """Shut down the shared worker pool (called during graceful shutdown)."""
    global _default_executor
    with _executor_lock:
        executor, _default_executor = _default_executor, None
    if executor is not None:
        executor.shutdown(wait=wait)


def _format_exc_traceback(exc: BaseException) -> str:
    """Format a captured exception with its full traceback.

    ``logger.log_trace`` reads the live ``sys.exc_info()`` and so only works
    inside an ``except`` block. Scheduling backstops receive the exception as an
    object (from ``task.exception()`` or a stored value) with no live context,
    so the traceback must be formatted from ``exc.__traceback__`` directly.
    """
    import traceback

    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        from evennia.utils.logger import log_err

        log_err(
            f"Unhandled error in scheduled coroutine:\n{_format_exc_traceback(exc)}"
        )


def _next_fire_time(target: float, interval: float, now: float) -> float:
    """Next fixed-rate fire time strictly after ``target``, never in the past.

    Anchors to ``target + interval``; if the body overran and that slot is
    already past ``now``, skips whole intervals to the next future slot so the
    loop resumes its cadence instead of firing a bunched catch-up burst.
    """
    target += interval
    if target <= now:
        missed = int((now - target) // interval) + 1
        target += missed * interval
    return target


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
        self._task = _create_runtime_task(loop, self._loop(now), "service")
        return self._stopped

    def stop(self):
        self.running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._stopped.set()

    async def _loop(self, fire_immediately: bool):
        try:
            loop = asyncio.get_running_loop()
            if fire_immediately:
                await self._run_once()
            # Fixed-RATE cadence like Twisted LoopingCall: anchor each wake to a
            # monotonic schedule so a slow body does not drift the period. If the
            # body overruns one or more intervals, skip to the next future slot
            # rather than firing a catch-up burst.
            target = loop.time()
            while self.running:
                target = _next_fire_time(target, self._interval, loop.time())
                await asyncio.sleep(target - loop.time())
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
            from evennia.utils.logger import log_trace

            if self._on_error is not None:
                try:
                    from twisted.python.failure import Failure

                    self._on_error(Failure())
                except Exception:
                    try:
                        self._on_error(None)
                    except Exception:
                        log_trace("LoopHandle on_error callback failed")
            else:
                log_trace("Unhandled error in repeating loop; loop stopped")
            self.running = False
            if self._task is not None and not self._task.done():
                self._task.cancel()
        finally:
            # Repeating roots are intentionally long-lived. Release any ORM
            # wrapper opened by this iteration rather than retaining one
            # PgBouncer client for the process lifetime.
            _close_database_connections()


class _SyncCoroutineResult:
    """Test-harness result when ``run_coroutine`` is called with no running loop."""

    def __init__(self, coro):
        try:
            prev_loop = asyncio.get_event_loop_policy().get_event_loop()
        except Exception:
            prev_loop = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        driver_token = _sync_driver_loop.set(loop)
        try:
            self._result = self._drive_inline(coro, loop)
            self._exception = None
        except Exception as exc:
            self._result = None
            self._exception = exc
            _log_task_exception_from_exception(exc)
        finally:
            # Match asyncio.run's teardown: cancel and drain any fire-and-forget
            # child tasks the coroutine spawned, so none are orphaned
            # ("Task was destroyed but it is pending"), then restore the loop.
            try:
                self._drain(loop)
            finally:
                _sync_driver_loop.reset(driver_token)
                loop.close()
                asyncio.set_event_loop(prev_loop)

    @staticmethod
    def _drive_inline(coro, loop):
        """Drive ``coro`` to completion by stepping it, off a running loop.

        This branch only runs in the no-loop test harness. Stepping the coroutine
        *body* with ``coro.send`` rather than handing the whole thing to
        ``asyncio.run`` is deliberate: Django binds the ORM to a distinct DB
        connection per running-loop context, so ORM work executed under a running
        loop lands on a connection that cannot see an enclosing test transaction,
        and a sqlite write then deadlocks against it ("database table is locked").
        Stepping inline runs the body in the caller's own context, keeping the ORM
        on the caller's connection.

        Suspension points are still honoured so this stays a faithful driver:

        * a bare ``None`` yield (``asyncio.sleep(0)`` / a plain reschedule) just
          continues — there are no competing tasks to yield to;
        * an already-settled Future feeds its result straight back;
        * a still-pending Future (a real timer/IO await) is run to completion on
          ``loop`` (only the awaited Future runs under the loop, never the body),
          then its result is fed back.

        ``loop`` is also the loop the body's ``ensure_future``/``create_task``
        calls register their children on (it is the current loop for the drive),
        so :meth:`_drain` can find and cancel them afterwards.
        """
        sent, throw = None, None
        token = _sync_inline_driver.set(True)
        try:
            while True:
                if throw is not None:
                    waiter, throw = coro.throw(throw), None
                else:
                    waiter = coro.send(sent)
                sent = None
                if waiter is None:
                    continue
                if isinstance(waiter, asyncio.Future):
                    if not waiter.done():
                        loop.run_until_complete(waiter)
                    try:
                        sent = waiter.result()
                    except Exception as exc:  # feed the failure back into the coro
                        throw = exc
                    continue
                # A non-Future, non-None yield has no meaning without a real
                # loop; surface it rather than silently mis-driving.
                coro.close()
                raise RuntimeError(
                    "run_coroutine drove a coroutine that yielded a non-awaitable "
                    f"{waiter!r} with no running event loop (test-harness context)."
                )
        except StopIteration as stop:
            return getattr(stop, "value", None)
        finally:
            _sync_inline_driver.reset(token)

    @staticmethod
    def _drain(loop):
        """Cancel and await any tasks the coroutine left pending on ``loop``."""
        try:
            pending = asyncio.all_tasks(loop)
        except RuntimeError:
            return
        if not pending:
            return
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

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

    log_err(f"Unhandled error in scheduled coroutine:\n{_format_exc_traceback(exc)}")


def call_later(seconds, fn, *args, **kwargs):
    """Schedule ``fn(*args, **kwargs)`` once, ``seconds`` from now.

    Returns:
        A cancellable handle (``asyncio.TimerHandle``: ``.cancel()``).
    """
    loop = _get_loop()
    _flush_when_running(loop)
    return loop.call_later(
        seconds,
        _run_runtime_callback,
        fn,
        args,
        kwargs,
        "generic",
        context=_isolated_database_context(),
    )


def call_from_thread(fn, *args, **kwargs):
    """Run ``fn`` on the reactor/loop thread from a worker thread (thread-safe)."""
    loop = _get_loop()
    return loop.call_soon_threadsafe(
        _run_runtime_callback,
        fn,
        args,
        kwargs,
        "generic",
        context=_isolated_database_context(),
    )


def when_running(fn, *args, **kwargs):
    """Run ``fn`` once the reactor/loop is running (or immediately if it is)."""
    try:
        loop = _get_loop()
    except RuntimeError:
        with _pending_lock:
            _pending_when_running.append((fn, args, kwargs))
        return None
    _flush_when_running(loop)
    return loop.call_soon(
        _run_runtime_callback,
        fn,
        args,
        kwargs,
        "generic",
        context=_isolated_database_context(),
    )


async def maybe_await(result):
    """Await a native or Twisted value under either coroutine driver.

    The async equivalent of Twisted ``inlineCallbacks``' lenient ``yield``: that
    idiom waits on a Deferred but passes a plain value straight through. When a
    generator that yields a *mix* of Deferreds and plain values (as the cmdset
    merge + command dispatch code does) is converted to ``async``/``await``,
    replace ``x = yield expr`` with ``x = await maybe_await(expr)`` so both cases
    keep working without per-yield analysis.

    Evennia still has compatibility callers driven by Twisted ``ensureDeferred``.
    A Twisted driver cannot consume a pending asyncio Future, while a native
    asyncio Task cannot consume a Deferred. Bridge the value in either direction
    at this shared boundary. Non-Deferred awaitables under a Twisted driver run as
    native asyncio Tasks, so Futures awaited inside their coroutine bodies are
    bridged too.
    """
    if not inspect.isawaitable(result):
        return result

    from twisted.internet.defer import Deferred

    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None

    if task is None and _sync_inline_driver.get():
        if isinstance(result, Deferred):
            result = result.asFuture(_get_loop())
    elif task is None and not isinstance(result, Deferred):
        future = (
            result
            if isinstance(result, asyncio.Future)
            else asyncio.ensure_future(result, loop=_get_loop())
        )
        result = Deferred.fromFuture(future)
    elif task is not None and isinstance(result, Deferred):
        result = result.asFuture(asyncio.get_running_loop())
    return await result


def _normalize_task_kind(value: str) -> str:
    """Return one bounded metric/debug label for a runtime root."""

    normalized = str(value or "generic").strip().lower()
    return normalized if normalized in _RUNTIME_TASK_KINDS else "generic"


def _isolated_database_context() -> contextvars.Context:
    """Copy application context while detaching inherited Django wrappers.

    ``asgiref.local.Local`` deliberately propagates values into child asyncio
    tasks. That is useful for tracing and application context, but unsafe for
    Django connection wrappers: detached roots would otherwise share a wrapper
    with their parent and keep PgBouncer client sockets alive after completion.

    Removing aliases inside the *copied* context preserves every non-database
    ContextVar while leaving the parent's wrappers untouched. New wrappers are
    then owned exclusively by the root and can be closed deterministically.
    """

    context = contextvars.copy_context()

    def detach() -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Before loop startup Django uses true thread-local storage. The
            # future async task will switch to its ContextVar-backed storage,
            # so there is nothing inherited to detach; deleting here would
            # instead mutate the bootstrap thread's synchronous namespace.
            return
        try:
            from django.core.exceptions import ImproperlyConfigured
            from django.db import connections

            local = connections._connections
            for alias in connections:
                try:
                    delattr(local, alias)
                except AttributeError:
                    pass
        except ImproperlyConfigured:
            # Tiny utility consumers may use the clock without Django.
            return
        except Exception as err:
            raise RuntimeError(
                "could not isolate Django state for runtime root"
            ) from err

    context.run(detach)
    return context


def _record_runtime_task(kind: str, event: str, had_connection: bool = False) -> None:
    """Emit optional low-cardinality task/connection lifecycle metrics."""

    try:
        from evennia.server.prometheus_metrics import record_runtime_task

        record_runtime_task(kind, event, had_connection=had_connection)
    except Exception:
        pass


def _close_database_connections() -> bool:
    """Close wrappers in the current execution context and report use."""

    try:
        from django.db import connections

        had_connection = any(
            wrapper.connection is not None
            for wrapper in connections.all(initialized_only=True)
        )
        connections.close_all()
        return had_connection
    except Exception:
        return False


def _run_runtime_callback(fn, args, kwargs, task_kind="generic"):
    """Run one synchronous loop callback under a closing DB scope."""

    kind = _normalize_task_kind(task_kind)
    token = _runtime_task_kind.set(kind)
    _record_runtime_task(kind, "started")
    result_kind = "completed"
    had_connection = False
    try:
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            return run_coroutine(result, task_kind=kind)
        return result
    except Exception:
        result_kind = "failed"
        raise
    finally:
        try:
            had_connection = _close_database_connections()
        finally:
            _record_runtime_task(kind, result_kind, had_connection=had_connection)
            _runtime_task_kind.reset(token)


def run_callback(fn, *args, _task_kind="generic", **kwargs):
    """Run a completion callback in an isolated synchronous DB scope."""

    context = _isolated_database_context()
    return context.run(_run_runtime_callback, fn, args, kwargs, _task_kind)


async def _run_runtime_root(coro, task_kind: str, started: list[bool]):
    """Run one detached root and release only its owned DB wrappers."""

    started[0] = True
    kind = _normalize_task_kind(task_kind)
    token = _runtime_task_kind.set(kind)
    _record_runtime_task(kind, "started")
    result = "completed"
    had_connection = False
    try:
        return await coro
    except asyncio.CancelledError:
        result = "cancelled"
        raise
    except Exception:
        result = "failed"
        raise
    finally:
        try:
            had_connection = _close_database_connections()
        finally:
            _record_runtime_task(kind, result, had_connection=had_connection)
            _runtime_task_kind.reset(token)


def _create_runtime_task(loop, coro, task_kind: str) -> asyncio.Task:
    """Create one supervised root on ``loop``, even before it is running."""

    context = _isolated_database_context()
    started = [False]
    task = loop.create_task(
        _run_runtime_root(coro, task_kind, started), context=context
    )

    def completed(done_task):
        # If cancellation wins before the wrapper's first bytecode executes,
        # asyncio closes the wrapper but not the user coroutine stored in its
        # arguments. Close it explicitly so delayed/action roots never produce
        # "coroutine was never awaited" or retain captured game objects.
        if not started[0] and inspect.iscoroutine(coro):
            coro.close()
        _log_task_exception(done_task)

    task.add_done_callback(completed)
    return task


def run_coroutine(coro, *, task_kind="generic"):
    """Schedule a detached runtime root on the event loop.

    The root receives an isolated Django connection context and closes every
    connection it owns on success, error, or cancellation. Application
    ContextVars (command tracing, game execution context) still propagate.

    Returns an ``asyncio.Task`` when a loop is running. When called from a test
    harness with no running loop, drives the coroutine synchronously and returns
    a lightweight result object.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if not is_io_owner():
            if inspect.iscoroutine(coro):
                coro.close()
            raise RuntimeError(
                "run_coroutine() without a running loop is available only to the IO owner"
            )
        return _SyncCoroutineResult(coro)
    return _create_runtime_task(loop, coro, task_kind)


def current_runtime_task_kind() -> str | None:
    """Return the owning detached runtime kind, if called inside one."""

    return _runtime_task_kind.get()


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

    _get_loop()
    return run_coroutine(_delayed(), task_kind="generic")


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
        # Each errback is invoked once with a Twisted Failure (the deferLater
        # contract). A deliberately-raised errback (e.g. taskhandler.handle_error
        # re-raising a non-cancel failure) is allowed to propagate to the loop's
        # exception handler, which surfaces it with a traceback, rather than
        # being swallowed or masked by a retry with a different argument shape.
        from twisted.python.failure import Failure

        failure = Failure(exc)
        for errfn, args, kwargs in self._errbacks:
            result = errfn(failure, *args, **kwargs)
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
