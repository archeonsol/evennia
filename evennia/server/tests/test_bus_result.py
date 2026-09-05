"""Publication completion must not imply command completion."""

import asyncio
import gc
import threading
from unittest import TestCase
from unittest.mock import patch

from evennia.server.bus_result import PublicationResult, TransportUnavailable


class TestPublicationResult(TestCase):
    """Exercise legacy callback and asyncio callers with explicit failures."""

    def test_rejection_callbacks_run_inline_without_dispatch(self):
        """Repeated rejected requests do not grow a stalled loop's callback queue."""
        seen = []
        for _ in range(1000):
            result = PublicationResult.rejected(TransportUnavailable("closed"))
            result.addErrback(lambda error: seen.append(error))
            self.assertFalse(result.admitted)
        self.assertEqual(len(seen), 1000)

    def test_published_result_supports_callback_and_await(self):
        """Successful completion reports Redis publication only."""
        result = PublicationResult()
        seen = []
        result.addCallback(seen.append)
        result.succeed("1-0")
        self.assertEqual(seen, ["1-0"])

        async def waiting():
            return await result

        self.assertEqual(asyncio.run(waiting()), "1-0")

    def test_logged_failure_still_fails_when_awaited(self):
        """An errback used for logging cannot convert rejection into success."""
        result = PublicationResult()
        result.addErrback(lambda error: None)
        result.fail(TransportUnavailable("uncertain"))

        async def waiting():
            return await result

        with self.assertRaises(TransportUnavailable):
            asyncio.run(waiting())

    def test_worker_cannot_register_game_callbacks(self):
        """Off-thread callback registration cannot create an unbounded handoff."""
        result = PublicationResult.rejected(TransportUnavailable("closed"))
        errors = []

        def worker():
            try:
                result.addErrback(lambda error: None)
            except RuntimeError as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)

    def test_canceling_one_waiter_preserves_publication(self):
        """A caller timeout cannot cancel another caller's publication result."""
        result = PublicationResult()
        seen = []
        result.addCallback(seen.append)

        async def waiting():
            async def observe():
                return await result

            canceled = asyncio.create_task(observe())
            surviving = asyncio.create_task(observe())
            await asyncio.sleep(0)
            canceled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await canceled
            self.assertFalse(result._future.cancelled())
            result.succeed("2-0")
            self.assertEqual(await surviving, "2-0")

        asyncio.run(waiting())
        self.assertEqual(seen, ["2-0"])
        self.assertEqual(result._callbacks, [])
        result.addCallback(seen.append)
        self.assertEqual(seen, ["2-0", "2-0"])

    def test_raising_observer_does_not_strand_later_observers(self):
        """Observer failures are logged without changing publication failure."""
        result = PublicationResult()
        error = TransportUnavailable("uncertain")
        seen = []

        def broken(_error):
            raise ValueError("observer failed")

        result.addErrback(broken).addErrback(seen.append)
        with patch("evennia.utils.logger.log_trace") as log:
            result.fail(error)
            result.addErrback(broken)
        self.assertEqual(log.call_count, 2)
        self.assertEqual(seen, [error])
        self.assertIs(result._future.exception(), error)
        self.assertEqual(result._callbacks, [])

    def test_failure_after_sole_waiter_cancels_is_observed(self):
        """Timing out a wait must not leave an unobserved asyncio exception."""
        result = PublicationResult()
        seen = []
        result.addErrback(seen.append)

        async def waiting():
            errors = []
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda _loop, context: errors.append(context))

            async def observe():
                return await result

            task = asyncio.create_task(observe())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            result.fail(TransportUnavailable("late failure"))
            for _ in range(4):
                await asyncio.sleep(0)
            gc.collect()
            self.assertEqual(errors, [])

        asyncio.run(waiting())
        self.assertEqual(len(seen), 1)
