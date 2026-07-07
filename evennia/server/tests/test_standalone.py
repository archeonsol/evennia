"""Tests for evennia.standalone() headless boot."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class StandaloneTest(SimpleTestCase):
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
    @patch("evennia.standalone.clock.stop_loop")
    def test_shutdown_standalone_stops_service(self, mock_stop_loop, mock_hooks):
        from evennia.standalone import StandaloneContext, shutdown_standalone

        application = MagicMock()
        application.running = True
        loop = MagicMock()
        loop.is_running.return_value = True
        loop.is_closed.return_value = False
        ctx = StandaloneContext(loop=loop, application=application, portal_mode=False)

        shutdown_standalone(ctx)

        mock_hooks.assert_called_once()
        application.stopService.assert_called_once()
        mock_stop_loop.assert_called_once()
