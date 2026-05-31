"""
Blessed helpers for running blocking I/O off the Twisted reactor thread.

Evennia is synchronous by default: command bodies, hooks, scripts and the rest
of the game run on the single reactor thread. A blocking call made on that
thread (an HTTP request, a slow file read, a third-party SDK call) freezes
*every* connected player for its full duration. These helpers are the one
blessed way to push that blocking work onto Twisted's reactor thread pool and
deliver the result back safely.

Threading-safety contract (read this before using):

The callable you pass to `in_thread` / `background` / `threaded` runs in a
*worker thread*, not on the reactor thread. In that worker it may do only:

- stdlib and network I/O (``requests``, ``socket``, file reads, subprocess);
- pure computation.

and it must return plain data (str / bytes / int / dict / list of primitives).

It must NOT touch game state in any way: no ``.db`` / ``.ndb`` access, no
typeclass attributes, no ``obj.msg(...)``, no manager/ORM queries through
typeclasses, no mutation of shared game state, no idmapper-cached instances.
Django's ORM is per-thread-connection safe, but Evennia's typeclass + idmapper
layer is not designed for concurrent access; treat all game-object access as
reactor-thread-only.

All game-object interaction happens *after* the worker returns, in the
Deferred's callback, which Twisted runs back on the reactor thread::

    from evennia.utils import defer

    def _fetch():                  # worker thread: pure I/O, no game objects
        return requests.get(url, timeout=10).json()

    def _deliver(data):            # reactor thread: safe to touch game objects
        caller.msg(f"Result: {data['field']}")

    def _failed(failure):          # reactor thread
        caller.msg("The request failed.")
        return None                # handled; don't re-raise

    defer.in_thread(_fetch).addCallbacks(_deliver, _failed)

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

from twisted.internet import threads
from twisted.internet.defer import Deferred

from evennia.utils import logger


def in_thread(fn, *args, **kwargs) -> Deferred:
    """
    Run a blocking, game-state-free callable in the reactor thread pool.

    Args:
        fn (callable): The callable to run in a worker thread. It must obey the
            threading-safety contract (see module docstring): pure I/O and
            computation only, returns plain data, touches no game objects.
        *args: Positional arguments passed to `fn`.
        **kwargs: Keyword arguments passed to `fn`.

    Returns:
        Deferred: A Twisted Deferred whose callbacks/errbacks run on the reactor
            thread. Attach `.addCallback` to handle the result and touch game
            objects safely.

    """
    return threads.deferToThread(fn, *args, **kwargs)


def background(fn, *args, on_error=None, **kwargs) -> None:
    """
    Fire-and-forget version of `in_thread`. No result is awaited.

    Use this for outbound work where you do not need the result back: webhooks,
    notifications, telemetry posts. The same threading-safety contract applies
    (see module docstring): `fn` runs in a worker thread and must not touch
    game state.

    Exceptions are never swallowed. If `fn` raises, `on_error(failure)` is
    called on the reactor thread when given; otherwise the failure is logged
    via the engine logger. Either way the error surfaces rather than vanishing.

    Args:
        fn (callable): The callable to run in a worker thread.
        *args: Positional arguments passed to `fn`.
        on_error (callable, optional): Called as `on_error(failure)` on the
            reactor thread if `fn` raises, where `failure` is a Twisted
            `Failure`. If not given, the failure is logged.
        **kwargs: Keyword arguments passed to `fn`.

    Returns:
        None: There is nothing to await; this is fire-and-forget.

    """

    def _on_error(failure):
        if on_error is not None:
            on_error(failure)
        else:
            logger.log_err(
                "defer.background task %s failed:\n%s"
                % (getattr(fn, "__name__", fn), failure.getTraceback())
            )
        # error handled here; stop it propagating into Twisted's unhandled-error log
        return None

    in_thread(fn, *args, **kwargs).addErrback(_on_error)


def threaded(fn):
    """
    Decorator marking a function as always-threaded.

    Calling the decorated function runs the original in a worker thread via
    `in_thread` and returns a Deferred. Sugar for functions that are always
    blocking I/O. The wrapped function must obey the threading-safety contract
    (see module docstring).

    Args:
        fn (callable): The function to wrap.

    Returns:
        callable: A wrapper that returns a Deferred when called.

    Example:
        ```python
        @threaded
        def fetch_status(url):
            return requests.get(url, timeout=10).status_code

        fetch_status(url).addCallback(lambda code: caller.msg(f"{code}"))
        ```

    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        return in_thread(fn, *args, **kwargs)

    return wrapper
