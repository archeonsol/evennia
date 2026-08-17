"""Tests for LauncherSession event-driven status waits."""

import struct
from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.server.launcher_ipc import (
    _MAX_FRAME_SIZE,
    LauncherIPCFrameError,
    LauncherSession,
    _decode_frames,
    _encode_frame,
    connect_session,
    wait_until_state,
)


class DecodeFramesTest(SimpleTestCase):
    def test_round_trip_single_frame(self):
        payload = {"type": "status", "status": [True, False, 1, None, {}, {}]}
        frames, remainder = _decode_frames(bytearray(_encode_frame(payload)))
        self.assertEqual(frames, [payload])
        self.assertEqual(remainder, bytearray())

    def test_partial_header_returns_no_frames(self):
        encoded = _encode_frame({"type": "status"})
        for n in (1, 2, 3):
            buffer = bytearray(encoded[:n])
            frames, remainder = _decode_frames(buffer)
            self.assertEqual(frames, [])
            self.assertEqual(remainder, bytearray(encoded[:n]))

    def test_partial_body_completes_on_rest(self):
        encoded = _encode_frame({"type": "status"})
        split = len(encoded) - 2
        buffer = bytearray(encoded[:split])
        frames, buffer = _decode_frames(buffer)
        self.assertEqual(frames, [])
        self.assertEqual(buffer, bytearray(encoded[:split]))
        buffer.extend(encoded[split:])
        frames, buffer = _decode_frames(buffer)
        self.assertEqual(frames, [{"type": "status"}])
        self.assertEqual(buffer, bytearray())

    def test_two_frames_in_one_buffer(self):
        first = {"type": "status"}
        second = {"type": "ack"}
        buffer = bytearray(_encode_frame(first) + _encode_frame(second))
        frames, remainder = _decode_frames(buffer)
        self.assertEqual(frames, [first, second])
        self.assertEqual(remainder, bytearray())

    def test_byte_by_byte_feed_yields_one_frame(self):
        encoded = _encode_frame({"type": "ack"})
        buffer = bytearray()
        collected = []
        for byte in encoded:
            buffer.append(byte)
            frames, buffer = _decode_frames(buffer)
            collected.extend(frames)
        self.assertEqual(collected, [{"type": "ack"}])
        self.assertEqual(buffer, bytearray())

    def test_oversized_length_raises_immediately(self):
        buffer = bytearray(struct.pack("!I", _MAX_FRAME_SIZE + 1))
        with self.assertRaises(LauncherIPCFrameError):
            _decode_frames(buffer)

    def test_malformed_json_body_raises(self):
        body = b"not json"
        buffer = bytearray(struct.pack("!I", len(body)) + body)
        with self.assertRaises(LauncherIPCFrameError):
            _decode_frames(buffer)


class LauncherSessionWaitTest(SimpleTestCase):
    def test_state_matches_respects_desired_flags(self):
        session = LauncherSession("127.0.0.1", 4006)
        status = [True, False, 1, None, {}, {}]
        self.assertTrue(session._state_matches(status, portal_running=True, server_running=False))
        self.assertFalse(session._state_matches(status, portal_running=True, server_running=True))

    @patch.object(LauncherSession, "_send")
    @patch.object(LauncherSession, "_read_frame")
    def test_query_status_skips_status_push(self, mock_read, _mock_send):
        mock_read.side_effect = [
            {"type": "status_push", "status": [True, False, 1, None, {}, {}]},
            {"type": "status", "status": [True, False, 1, None, {}, {}]},
        ]
        session = LauncherSession("127.0.0.1", 4006)
        result = session.query_status()
        self.assertEqual(result, [True, False, 1, None, {}, {}])
        self.assertEqual(mock_read.call_count, 2)

    @patch.object(LauncherSession, "read_push", return_value=None)
    @patch.object(LauncherSession, "query_status")
    def test_wait_for_state_returns_on_first_match(self, mock_query, _mock_push):
        mock_query.return_value = [True, True, 1, 2, {}, {}]
        session = LauncherSession("127.0.0.1", 4006)
        result = session.wait_for_state(timeout=1.0, poll_interval=0.01)
        self.assertEqual(result, mock_query.return_value)

    @patch.object(LauncherSession, "read_push", return_value=[True, True, 1, 2, {}, {}])
    @patch.object(LauncherSession, "query_status", return_value=[False, False, 1, 2, {}, {}])
    def test_wait_for_state_returns_on_push_match(self, _mock_query, mock_push):
        # query never matches; the state arrives on the push branch of the loop.
        session = LauncherSession("127.0.0.1", 4006)
        result = session.wait_for_state(timeout=1.0, poll_interval=0.01)
        self.assertEqual(result, mock_push.return_value)

    @patch.object(LauncherSession, "read_push", return_value=[False, False, 1, 2, {}, {}])
    @patch.object(LauncherSession, "query_status", return_value=[False, False, 1, 2, {}, {}])
    def test_wait_for_state_times_out_returns_none(self, _mock_query, _mock_push):
        # neither query nor push ever matches → the deadline exit returns None.
        session = LauncherSession("127.0.0.1", 4006)
        result = session.wait_for_state(timeout=0.05, poll_interval=0.02)
        self.assertIsNone(result)

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

    @patch.object(LauncherSession, "connect", side_effect=ConnectionError("refused"))
    def test_wait_until_state_portal_down_on_ipc_refused(self, _mock_connect):
        with patch("evennia.server.launcher_ipc.time.sleep"):
            status = wait_until_state(
                "127.0.0.1",
                4006,
                portal_running=False,
                deadline=1.0,
                poll_interval=0.01,
            )
        self.assertEqual(status, [False, False, None, None, {}, {}])

    @patch("evennia.server.launcher_ipc.portal_ipc_reachable", side_effect=[True, True, False])
    def test_wait_for_portal_ipc_down(self, _mock_reachable):
        with patch("evennia.server.launcher_ipc.time.sleep"):
            from evennia.server.launcher_ipc import wait_for_portal_ipc_down

            self.assertTrue(wait_for_portal_ipc_down("127.0.0.1", 4006, deadline=1.0))

    @patch.object(LauncherSession, "_send")
    @patch.object(LauncherSession, "_read_frame")
    @patch.object(LauncherSession, "connect")
    def test_query_ipc_status(self, _mock_connect, mock_read, _mock_send):
        from evennia.server.launcher_ipc import query_ipc_status

        mock_read.return_value = {
            "type": "status",
            "status": [True, False, 1, None, {}, {}],
        }
        result = query_ipc_status("127.0.0.1", 4006)
        self.assertEqual(result, [True, False, 1, None, {}, {}])

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

        saved_loop, saved_tid = clock._main_loop, clock._loop_thread_id
        loop = asyncio.new_event_loop()
        clock.bind_loop(loop)
        portal = MagicMock()
        portal.info_dict = {}
        factory = MagicMock()
        protocol = MagicMock()
        protocol.send_Status2Launcher = MagicMock()

        try:
            launcher_ipc.start_launcher_server(portal, factory, protocol, "127.0.0.1", 0)
            self.assertTrue(launcher_ipc._servers)
        finally:
            for server in launcher_ipc._servers:
                server.close()
            launcher_ipc._servers = []
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            # Restore the prior bound-loop identity; binding a fresh unrun loop
            # here would leave later tests (e.g. utils.tests.test_defer) resolving
            # defer_to_thread onto a loop that never runs their callbacks.
            clock._main_loop, clock._loop_thread_id = saved_loop, saved_tid

    def test_stop_launcher_servers_isolates_failures_and_clears_registry(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from evennia.server import launcher_ipc

        failed = MagicMock()
        failed.wait_closed = AsyncMock(side_effect=RuntimeError("failed"))
        sibling = MagicMock()
        sibling.wait_closed = AsyncMock()
        launcher_ipc._servers = [failed, sibling]

        with patch.object(launcher_ipc.logger, "log_err") as log_err:
            asyncio.run(launcher_ipc.stop_launcher_servers())

        failed.close.assert_called_once()
        sibling.close.assert_called_once()
        self.assertEqual(launcher_ipc._servers, [])
        self.assertTrue(log_err.called)

    def test_cancelled_start_closes_post_bind_launcher_listener(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from evennia.server import launcher_ipc
        from evennia.utils import clock

        listener = MagicMock()
        listener.wait_closed = AsyncMock()
        portal = MagicMock()
        portal.info_dict = {}

        async def exercise():
            loop = asyncio.get_running_loop()
            bound = loop.create_future()

            async def create_server(*_args, **_kwargs):
                return await bound

            with (
                patch.object(clock, "get_bound_loop", return_value=loop),
                patch.object(loop, "create_server", side_effect=create_server),
            ):
                task = asyncio.create_task(
                    launcher_ipc._start_server(portal, MagicMock(), MagicMock(), "127.0.0.1", 0)
                )
                await asyncio.sleep(0)
                bound.set_result(listener)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled())

        asyncio.run(exercise())

        listener.close.assert_called_once()
        listener.wait_closed.assert_awaited_once()
        self.assertNotIn(listener, launcher_ipc._servers)
