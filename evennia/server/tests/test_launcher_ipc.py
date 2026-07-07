"""Tests for LauncherSession event-driven status waits."""

from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.server.launcher_ipc import LauncherSession


class LauncherSessionWaitTest(SimpleTestCase):
    def test_state_matches_respects_desired_flags(self):
        session = LauncherSession("127.0.0.1", 4006)
        status = [True, False, 1, None, {}, {}]
        self.assertTrue(session._state_matches(status, portal_running=True, server_running=False))
        self.assertFalse(session._state_matches(status, portal_running=True, server_running=True))

    @patch.object(LauncherSession, "read_push", return_value=None)
    @patch.object(LauncherSession, "query_status")
    def test_wait_for_state_returns_on_first_match(self, mock_query, _mock_push):
        mock_query.return_value = [True, True, 1, 2, {}, {}]
        session = LauncherSession("127.0.0.1", 4006)
        result = session.wait_for_state(timeout=1.0, poll_interval=0.01)
        self.assertEqual(result, mock_query.return_value)

    @patch.object(LauncherSession, "read_push", return_value=[True, False, 1, None, {}, {}])
    def test_wait_for_push_returns_status_push(self, mock_push):
        session = LauncherSession("127.0.0.1", 4006)
        self.assertEqual(session.wait_for_push(timeout=1.0), mock_push.return_value)
