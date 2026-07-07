"""
Blessed helpers for running blocking I/O off the event loop thread.

Evennia is synchronous by default: command bodies, hooks, scripts and the rest
of the game run on the single event loop thread. A blocking call made on that
thread (an HTTP request, a slow file read, a third-party SDK call) freezes
*every* connected player for its full duration. These helpers are the one
blessed way to push that blocking work onto a thread pool and deliver the
result back safely.

Threading-safety contract (read this before using):

The callable you pass to `in_thread` / `background` / `threaded` runs in a
*worker thread*, not on the event loop thread. In that worker it may do only:

- stdlib and network I/O (``requests``, ``socket``, file reads, subprocess);
- direct Django ORM queries on plain (non-typeclass) models. The helper runs
  ``close_old_connections()`` around the worker, so ORM use in the pool does
  not accumulate or reuse stale connections (no "server has gone away");
- pure computation.

and it must return plain data (str / bytes / int / dict / list of primitives) —
convert ORM results to primitives before returning, rather than handing back
live model instances whose lazy fields would load on the event loop thread.

It must NOT touch game state in any way: no ``.db`` / ``.ndb`` access, no
typeclass attributes, no ``obj.msg(...)``, no queries through typeclass
managers, no mutation of shared game state, no idmapper-cached instances.
Django's ORM is per-thread-connection safe (and the helper keeps those
connections clean), but Evennia's typeclass + idmapper layer is not designed
for concurrent access; treat all game-object access as event-loop-thread-only.

All game-object interaction happens *after* the worker returns, in an
``await`` or Future callback, which runs back on the event loop thread::

    from evennia.utils import defer

    def _fetch():                  # worker thread: pure I/O, no game objects
        return requests.get(url, timeout=10).json()

    async def _deliver(data):      # event loop thread: safe to touch game objects
        caller.msg(f"Result: {data['field']}")

    defer.in_thread(_fetch).add_done_callback(
        lambda fut: asyncio.create_task(_deliver(fut.result()))
    )

What this does and does not solve:

This makes "do blocking work, then deliver a result or side effect later" safe:
commands, scripts, periodic jobs, outbound webhooks, fire-and-forget
notifications. It does NOT make blocking I/O safe inside a hook that must return
a value synchronously to its caller (``at_pre_move`` returning a bool to veto a
move, a lock function, a parser that must produce a result now). You cannot
offload those without breaking the caller's contract. For those: do not block.
Precompute, cache, or restructure so the I/O happens in a deferrable context.
"""

from functools import wraps

from django.db import close_old_connections

from evennia.utils import clock, logger


def _run_with_db_hygiene(fn, args, kwargs):
    """
    Worker-thread entry point: run `fn` with Django connection hygiene.

    Runs in the thread pool. ``close_old_connections`` is called before
    and after `fn` so a pooled worker thread never reuses a stale DB connection
    or leaves one open between jobs. Respects ``CONN_MAX_AGE`` (persistent
    connections are kept until they age out). Cheap and harmless for workers
    that never touch the ORM.
    """
    close_old_connections()
    try:
        return fn(*args, **kwargs)
    finally:
        close_old_connections()


def in_thread(fn, *args, **kwargs):
    """
    Run a blocking, game-state-free callable in the thread pool.

    Django connection hygiene is handled for you: `fn` may make direct ORM
    queries on plain (non-typeclass) models without leaking or reusing stale
    pool connections (see the module docstring's threading-safety contract).

    Args:
        fn (callable): The callable to run in a worker thread. It must obey the
            threading-safety contract (see module docstring): I/O, plain ORM,
            and computation only; returns plain data; touches no game objects.
        *args: Positional arguments passed to `fn`.
        **kwargs: Keyword arguments passed to `fn`.

    Returns:
        asyncio.Future: Whose result is delivered on the event loop thread.
            ``await`` it or attach ``.add_done_callback`` to handle the result
            and touch game objects safely.

    """
    return clock.defer_to_thread(_run_with_db_hygiene, fn, args, kwargs)


def _attach_done_errback(future, on_error=None):
    """Route executor failures to ``on_error`` or the engine logger."""

    def _done(fut):
        exc = fut.exception()
        if exc is None:
            return
        if on_error is not None:
            try:
                from twisted.python.failure import Failure

                on_error(Failure(exc))
            except Exception:
                on_error(exc)
        else:
            logger.log_err(f"defer.background task failed:\n{exc}")

    future.add_done_callback(_done)


def background(fn, *args, on_error=None, **kwargs) -> None:
    """
    Fire-and-forget version of `in_thread`. No result is awaited.

    Use this for outbound work where you do not need the result back: webhooks,
    notifications, telemetry posts. The same threading-safety contract applies
    (see module docstring): `fn` runs in a worker thread and must not touch
    game state.

    Exceptions are never swallowed. If `fn` raises, `on_error(failure)` is
    called on the event loop thread when given; otherwise the failure is logged
    via the engine logger. Either way the error surfaces rather than vanishing.

    Args:
        fn (callable): The callable to run in a worker thread.
        *args: Positional arguments passed to `fn`.
        on_error (callable, optional): Called as `on_error(failure)` on the
            event loop thread if `fn` raises, where `failure` is a Twisted
            `Failure`. If not given, the failure is logged.
        **kwargs: Keyword arguments passed to `fn`.

    Returns:
        None: There is nothing to await; this is fire-and-forget.

    """
    _attach_done_errback(in_thread(fn, *args, **kwargs), on_error=on_error)


def threaded(fn):
    """
    Decorator marking a function as always-threaded.

    Calling the decorated function runs the original in a worker thread via
    `in_thread` and returns a Future. Sugar for functions that are always
    blocking I/O. The wrapped function must obey the threading-safety contract
    (see module docstring).

    Args:
        fn (callable): The function to wrap.

    Returns:
        callable: A wrapper that returns a Future when called.

    Example:
        ```python
        @threaded
        def fetch_status(url):
            return requests.get(url, timeout=10).status_code

        fetch_status(url).add_done_callback(
            lambda fut: caller.msg(f"{fut.result()}")
        )
        ```

    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        return in_thread(fn, *args, **kwargs)

    return wrapper
