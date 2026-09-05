"""Explicit publication results for event-loop callers of the Redis bus."""

import asyncio
import threading
from concurrent.futures import Future


class TransportUnavailable(RuntimeError):
    """The frame was rejected or its publication outcome is uncertain."""


class PublicationResult:
    """Track admission and publication, never completion of a game action.

    Callback registration and settlement belong to the event-loop thread.
    Awaiting is lazy and uses the caller's current asyncio loop.
    """

    def __init__(self, admitted=True):
        """Create a pending publication result."""
        self.admitted = admitted
        self._future = Future()
        self._callbacks = []

    @classmethod
    def rejected(cls, error):
        """Return an already failed admission without scheduling callbacks."""
        result = cls(admitted=False)
        result.fail(error)
        return result

    def __bool__(self):
        """Report admission separately from publication completion."""
        return self.admitted

    def __await__(self):
        """Await publication on the current loop without allocating one early."""

        async def wait():
            future = asyncio.wrap_future(self._future)
            # A canceled observer must not leave the shared outcome unobserved.
            future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            return await asyncio.shield(future)

        return wait().__await__()

    def _add(self, failed, callback, args, kwargs):
        """Register only from the game loop, including already settled results."""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Redis bus callbacks must be registered on the event-loop thread")
        item = (failed, callback, args, kwargs)
        if self._future.done():
            self._invoke(item)
        else:
            self._callbacks.append(item)
        return self

    def addCallback(self, callback, *args, **kwargs):
        """Run a callback after publication succeeds."""
        return self._add(False, callback, args, kwargs)

    def addErrback(self, callback, *args, **kwargs):
        """Observe failure without changing its awaitable outcome."""
        return self._add(True, callback, args, kwargs)

    def _invoke(self, item):
        """Deliver the matching outcome on the settlement or registration thread."""
        failed, callback, args, kwargs = item
        error = self._future.exception()
        if failed == (error is not None):
            try:
                callback(error if failed else self._future.result(), *args, **kwargs)
            except Exception:
                from evennia.utils import logger

                logger.log_trace("redis bus: publication observer failed")

    def _finish(self):
        """Release registered callbacks after a single settlement."""
        callbacks, self._callbacks = self._callbacks, []
        for item in callbacks:
            self._invoke(item)

    def succeed(self, value=None):
        """Settle publication once with the Redis entry ID."""
        if not self._future.done():
            self._future.set_result(value)
            self._finish()

    def fail(self, error):
        """Settle rejection or uncertainty once."""
        if not self._future.done():
            self._future.set_exception(error)
            self._finish()
