"""Tests for evennia.standalone() headless boot."""

import asyncio
import threading
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class StandaloneTest(SimpleTestCase):
    def setUp(self):
        """Preserve process globals changed by an in-process bootstrap."""
        super().setUp()
        import evennia
        from evennia.utils import clock

        self._saved_application = getattr(evennia, "TWISTED_APPLICATION", None)
        self._saved_bound_loop = clock._main_loop
        self._saved_bound_thread = clock._loop_thread_id
        try:
            self._saved_event_loop = asyncio.get_event_loop()
        except RuntimeError:
            self._saved_event_loop = None

    def tearDown(self):
        """Restore loop ownership and the application after each test."""
        import evennia
        from evennia.utils import clock

        evennia.TWISTED_APPLICATION = self._saved_application
        clock._main_loop = self._saved_bound_loop
        clock._loop_thread_id = self._saved_bound_thread
        asyncio.set_event_loop(self._saved_event_loop)
        super().tearDown()

    @patch("evennia._init")
    @patch("django.setup")
    def test_standalone_binds_loop_and_starts_service(self, mock_setup, mock_init):
        import evennia
        from evennia.standalone import standalone

        loop = MagicMock()
        loop.is_running.return_value = False
        loop.is_closed.return_value = False
        application = MagicMock()
        application.running = True
        evennia.TWISTED_APPLICATION = application

        with (
            patch("evennia.standalone.asyncio.new_event_loop", return_value=loop),
            patch("evennia.standalone.asyncio.set_event_loop"),
            patch("evennia.standalone.clock.bind_loop") as mock_bind,
        ):
            ctx = standalone(portal_mode=False, start_loop=False)

        mock_setup.assert_called_once()
        mock_init.assert_called_once_with(portal_mode=False)
        mock_bind.assert_called_once_with(loop)
        application.startService.assert_called_once()
        self.assertIs(ctx.loop, loop)
        self.assertIs(ctx.application, application)
        self.assertFalse(ctx.portal_mode)

    @patch("evennia.standalone.clock.run_shutdown_hooks")
    @patch("evennia.standalone.clock.cancel_pending_tasks")
    @patch("evennia.standalone.clock.shutdown_default_executor")
    def test_shutdown_standalone_stops_service(self, mock_executor, mock_cancel, mock_hooks):
        from evennia.standalone import StandaloneContext, shutdown_standalone

        application = MagicMock()
        application.running = True
        loop = MagicMock()
        loop.is_running.return_value = False
        loop.is_closed.return_value = False
        ctx = StandaloneContext(loop=loop, application=application, portal_mode=False)

        shutdown_standalone(ctx)

        mock_hooks.assert_called_once_with(shutdown_executor=False)
        application.stopService.assert_called_once()
        mock_cancel.assert_called_once_with(loop)
        mock_executor.assert_called_once()
        loop.close.assert_called_once()

    @patch("evennia._init")
    @patch("django.setup")
    def test_background_binds_and_starts_service_on_loop_thread(self, mock_setup, mock_init):
        import evennia
        from evennia.standalone import shutdown_standalone, standalone
        from evennia.utils import clock
        from evennia.utils.utils import run_in_main_thread

        observed = {}
        application = MagicMock()
        application.running = True

        def started():
            observed["thread"] = threading.get_ident()
            observed["owner"] = clock.is_io_owner()

        application.startService.side_effect = started
        evennia.TWISTED_APPLICATION = application

        ctx = standalone(portal_mode=False, start_loop=True, background=True)
        try:
            self.assertTrue(ctx.loop.is_running())
            self.assertEqual(observed["thread"], ctx.thread.ident)
            self.assertTrue(observed["owner"])
            self.assertNotEqual(threading.get_ident(), ctx.thread.ident)
            self.assertFalse(clock.is_io_owner())
            self.assertEqual(run_in_main_thread(threading.get_ident), ctx.thread.ident)

            root_started = threading.Event()

            async def pending_root():
                root_started.set()
                await asyncio.Event().wait()

            def start_root():
                observed["task"] = clock.run_coroutine(pending_root(), task_kind="test")

            run_in_main_thread(start_root)
            self.assertTrue(root_started.wait(timeout=1))
        finally:
            shutdown_standalone(ctx)

        self.assertFalse(ctx.thread.is_alive())
        self.assertTrue(observed["task"].cancelled())
        application.stopService.assert_called_once()

    @patch("evennia._init")
    @patch("django.setup")
    def test_background_startup_error_is_returned_without_leaks(self, mock_setup, mock_init):
        import evennia
        from evennia.standalone import standalone

        application = MagicMock()
        application.startService.side_effect = RuntimeError("startup failed")
        evennia.TWISTED_APPLICATION = application

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            standalone(portal_mode=False, start_loop=True, background=True)

        application.stopService.assert_called_once()
