"""Tests for the engine login-start screen op (``evennia.actions.default.loginstart``)."""

import unittest
from unittest import mock

from evennia.actions.default import loginstart
from evennia.actions.tests.fakes import FakeSession


class TestRenderConnectionScreen(unittest.TestCase):
    """``render_connection_screen`` resolves the configured screen, with fallback."""

    def test_renders_via_connection_screen_callable(self):
        session = FakeSession()
        with mock.patch.object(
            loginstart.utils,
            "callables_from_module",
            return_value={"connection_screen": lambda: "WELCOME"},
        ):
            loginstart.render_connection_screen(session)
        self.assertEqual(session.messages, ["WELCOME"])

    def test_renders_random_string_when_no_callable(self):
        session = FakeSession()
        with (
            mock.patch.object(loginstart.utils, "callables_from_module", return_value={}),
            mock.patch.object(loginstart.utils, "random_string_from_module", return_value="SCREEN"),
        ):
            loginstart.render_connection_screen(session)
        self.assertEqual(session.messages, ["SCREEN"])

    def test_fallback_notice_when_empty(self):
        session = FakeSession()
        with (
            mock.patch.object(loginstart.utils, "callables_from_module", return_value={}),
            mock.patch.object(loginstart.utils, "random_string_from_module", return_value=""),
        ):
            loginstart.render_connection_screen(session)
        self.assertTrue(any("No connection screen" in m for m in session.messages))


if __name__ == "__main__":
    unittest.main()
