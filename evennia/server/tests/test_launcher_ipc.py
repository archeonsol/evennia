"""Tests for LauncherSession event-driven status waits."""

from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.server.launcher_ipc import LauncherSession, connect_session, wait_until_state


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

    @patch.object(LauncherSession, "connect")
    def test_connect_session_retries_until_success(self, mock_connect):
        mock_connect.side_effect = [ConnectionError("refused"), ConnectionError("refused"), None]
        with patch("evennia.server.launcher_ipc.time.sleep"):
            session = connect_session("127.0.0.1", 4006, connect_timeout=5.0)
        self.assertIsInstance(session, LauncherSession)
        self.assertEqual(mock_connect.call_count, 3)

    @patch.object(LauncherSession, "connect", side_effect=ConnectionError("refused"))
    def test_connect_session_raises_after_timeout(self, _mock_connect):
        with patch("evennia.server.launcher_ipc.time.sleep"):
            with self.assertRaises(ConnectionError):
                connect_session("127.0.0.1", 4006, connect_timeout=0.1, retry_interval=0.01)

    @patch.object(LauncherSession, "wait_for_state", return_value=[True, False, 1, None, {}, {}])
    @patch.object(LauncherSession, "connect")
    def test_wait_until_state_returns_on_connect(self, _mock_connect, _mock_wait):
        with patch("evennia.server.launcher_ipc.time.sleep"):
            status = wait_until_state("127.0.0.1", 4006, deadline=1.0, poll_interval=0.01)
        self.assertEqual(status, [True, False, 1, None, {}, {}])

    def test_start_launcher_server_binds_before_run_forever(self):
        import asyncio
        from unittest.mock import MagicMock

        from evennia.server import launcher_ipc
        from evennia.utils import clock

        loop = asyncio.new_event_loop()
        clock.bind_loop(loop)
        portal = MagicMock()
        portal.info_dict = {}
        factory = MagicMock()
        protocol = MagicMock()
        protocol.send_Status2Launcher = MagicMock()

        try:
            launcher_ipc.start_launcher_server(
                portal, factory, protocol, "127.0.0.1", 0
            )
            self.assertTrue(launcher_ipc._servers)
        finally:
            for server in launcher_ipc._servers:
                server.close()
            launcher_ipc._servers = []
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            clock.bind_loop(asyncio.new_event_loop())
