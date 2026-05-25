"""
Off-reactor worker pool for CPU/IO-heavy tasks (help index, search rebuild, HTTP, etc.).

Use ``defer_to_worker`` instead of blocking the Twisted reactor. Results must be
applied back on the reactor thread (``reactor.callFromThread`` or a callback that
only schedules reactor-safe work).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from django.conf import settings
from twisted.internet import threads
from twisted.internet.defer import Deferred

_warn_ms = None


def _block_warn_ms() -> float:
    global _warn_ms
    if _warn_ms is None:
        _warn_ms = float(getattr(settings, "ENGINE_WORKER_BLOCK_WARN_MS", 50) or 50)
    return _warn_ms


def worker_pool_enabled() -> bool:
    return bool(getattr(settings, "ENGINE_WORKER_POOL_ENABLED", True))


def defer_to_worker(
    func: Callable,
    *args,
    callback: Optional[Callable] = None,
    errback: Optional[Callable] = None,
    **kwargs,
) -> Deferred:
    """
    Run ``func(*args, **kwargs)`` in a worker thread and return a Twisted ``Deferred``.

    Optional ``callback`` / ``errback`` are chained on the returned deferred.
    """
    if not worker_pool_enabled():
        raise RuntimeError("ENGINE_WORKER_POOL_ENABLED is False")

    started = time.perf_counter()

    def _wrapped():
        return func(*args, **kwargs)

    d = threads.deferToThread(_wrapped)
    if callback:
        d.addCallback(callback)
    if errback:
        d.addErrback(errback)

    def _check_elapsed(_result):
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        threshold = _block_warn_ms()
        if threshold > 0 and elapsed_ms > threshold * 10:
            try:
                from evennia.utils import logger

                logger.log_warn(
                    "defer_to_worker %s took %.0fms in thread (check for sync blocking on reactor)"
                    % (getattr(func, "__name__", func), elapsed_ms)
                )
            except Exception:
                pass
        return _result

    d.addBoth(_check_elapsed)
    return d


def schedule_on_reactor(func: Callable, *args, **kwargs) -> None:
    """Schedule ``func`` on the reactor thread (safe after worker completion)."""
    from twisted.internet import reactor

    reactor.callFromThread(lambda: func(*args, **kwargs))
