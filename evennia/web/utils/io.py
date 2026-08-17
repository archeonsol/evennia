"""Synchronous bridge from web workers to Evennia's bound IO loop."""

from __future__ import annotations

import concurrent.futures
import inspect
from collections.abc import Callable
from typing import Any


class IOThreadCallTimeout(TimeoutError):
    """A queued IO-thread call timed out and was cancelled before starting."""


class IOThreadCallIndeterminate(IOThreadCallTimeout):
    """An IO-thread call timed out after starting, leaving its outcome unknown."""


class IOThreadCallUnavailable(RuntimeError):
    """No IO owner is available to service a worker-thread call."""


def release_worker_db_connections() -> None:
    """Release worker reads before an owner-side database call.

    Django reconnects lazily for response and audit work. Calls executing on
    the IO owner retain its connection.
    """
    from django.db import connections

    from evennia.utils import clock

    if not clock.is_io_owner():
        for connection in connections.all():
            if connection.in_atomic_block:
                raise RuntimeError(
                    "An IO-owner database call cannot start inside a worker transaction"
                )
            if connection.vendor == "sqlite" and connection.connection is not None:
                # Django deliberately refuses to close shared in-memory test
                # databases. An explicit rollback still releases their read locks.
                connection.rollback()
        connections.close_all()


def _call_synchronously(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a synchronous callback and reject awaitable results."""
    result = callback(*args, **kwargs)
    if not inspect.isawaitable(result):
        return result
    cancel = getattr(result, "cancel", None)
    close = getattr(result, "close", None)
    if callable(cancel):
        cancel()
    elif callable(close):
        close()
    raise TypeError("run_on_io_thread requires a synchronous callable")


def run_on_io_thread(
    callback: Callable[..., Any], *args: Any, timeout: float = 15.0, **kwargs: Any
) -> Any:
    """Execute a synchronous callback on Evennia's bound IO loop.

    Calls made by the IO owner, including the actual main thread before loop
    binding, execute inline. Worker calls are scheduled through
    :mod:`evennia.utils.clock`, whose callback scope isolates Django database
    connections from the worker.

    Args:
        callback: Synchronous callable to execute.
        *args: Positional callback arguments.
        timeout: Maximum seconds the worker waits for completion.
        **kwargs: Keyword callback arguments.

    Returns:
        Any: The callback's return value.

    Raises:
        IOThreadCallTimeout: The callback was cancelled before it started.
        IOThreadCallIndeterminate: The callback started but did not finish
            before the timeout, so mutation callers must not retry it.
        IOThreadCallUnavailable: No bound owner can service a worker call.
        TypeError: The callback returned an awaitable.

    """
    from evennia.utils import clock

    if clock.is_io_owner():
        return _call_synchronously(callback, *args, **kwargs)
    loop = clock.get_bound_loop()
    if loop is None:
        raise IOThreadCallUnavailable(
            "Evennia IO owner is unavailable before loop binding on this worker thread"
        )

    future: concurrent.futures.Future = concurrent.futures.Future()

    def _wrapper():
        """Run the callback once unless the waiting worker cancelled it."""
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(_call_synchronously(callback, *args, **kwargs))
        except BaseException as err:
            future.set_exception(err)

    clock.call_from_thread(_wrapper)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as err:
        if future.cancel():
            raise IOThreadCallTimeout(
                "IO-thread call timed out and was cancelled before execution"
            ) from err
        if future.done():
            return future.result()
        raise IOThreadCallIndeterminate(
            "IO-thread call timed out after execution started; outcome is unknown"
        ) from err
