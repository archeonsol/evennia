"""Tests for the uvicorn ASGI web service lifecycle."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from evennia.server.asgi_webserver import UvicornWebService


class UvicornWebServiceStopTest(SimpleTestCase):
    def test_on_stop_signals_exit_and_clears_handles(self):
        svc = UvicornWebService(port=4005)
        server = MagicMock()
        thread = MagicMock()
        thread.is_alive.return_value = False  # already exited; no join to wait on
        svc._server = server
        svc._thread = thread

        svc._on_stop()

        self.assertTrue(server.should_exit)  # asked uvicorn to exit
        self.assertIsNone(svc._server)  # handles dropped so a restart rebuilds
        self.assertIsNone(svc._thread)

    def test_on_stop_is_idempotent_when_never_started(self):
        svc = UvicornWebService(port=4005)  # _server / _thread are None
        svc._on_stop()  # must not raise on the never-started service
        self.assertIsNone(svc._server)
        self.assertIsNone(svc._thread)
