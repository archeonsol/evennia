"""Tests for launcher ↔ Portal asyncio IPC (T3 S10)."""

import asyncio
import json
import struct
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings


class LauncherIPCFrameTest(SimpleTestCase):
    def test_encode_decode_roundtrip(self):
        from evennia.server.launcher_ipc import _decode_frames, _encode_frame

        payload = {"type": "command", "operation": "\x0f", "arguments": ""}
        buf = bytearray(_encode_frame(payload))
        frames, rest = _decode_frames(buf)
        self.assertEqual(frames, [payload])
        self.assertEqual(len(rest), 0)

    def test_partial_buffer_waits_for_more_bytes(self):
        from evennia.server.launcher_ipc import _decode_frames, _encode_frame

        body = _encode_frame({"type": "ack"})
        buf = bytearray(body[:6])
        frames, rest = _decode_frames(buf)
        self.assertEqual(frames, [])
        buf.extend(body[6:])
        frames, rest = _decode_frames(buf)
        self.assertEqual(frames[0]["type"], "ack")


class LauncherSessionTest(SimpleTestCase):
    def test_query_status_and_command_ack(self):
        from evennia.server.launcher_ipc import _encode_frame, LauncherSession

        received = []

        async def _handle(reader, writer):
            while not reader.at_eof():
                frame = await reader.readexactly(4)
                (size,) = struct.unpack("!I", frame)
                payload = json.loads((await reader.readexactly(size)).decode("utf-8"))
                received.append(payload)
                if payload["type"] == "status":
                    writer.write(
                        _encode_frame({"type": "status", "status": [True, False, 1, 0, {}, {}]})
                    )
                elif payload["type"] == "command":
                    writer.write(_encode_frame({"type": "ack"}))
                await writer.drain()

        async def _run():
            server = await asyncio.start_server(_handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]

            def _client():
                session = LauncherSession("127.0.0.1", port, timeout=5)
                session.connect()
                status = session.query_status()
                self.assertEqual(status[0], True)
                session.send_command_fire("\x0f", ["python", "server.py"])
                session.close()

            await asyncio.to_thread(_client)
            server.close()
            await server.wait_closed()

        asyncio.run(_run())
        self.assertEqual(received[0]["type"], "status")
        self.assertEqual(received[1]["type"], "command")


@override_settings(EVENNIA_ASYNCIO_BOOTSTRAP=True)
class LauncherIPCIntegrationTest(SimpleTestCase):
    def test_dispatch_status_frame(self):
        from evennia.server.launcher_ipc import _LauncherIPCProtocol

        protocol = MagicMock()
        protocol.get_status.return_value = (True, False, 99, 0, {"p": 1}, {})
        factory = MagicMock()
        amp_protocol = protocol

        ipc = _LauncherIPCProtocol(MagicMock(), factory, amp_protocol)
        transport = MagicMock()
        ipc.connection_made(transport)
        ipc.data_received(
            __import__("evennia.server.launcher_ipc", fromlist=["_encode_frame"])._encode_frame(
                {"type": "status"}
            )
        )
        transport.write.assert_called_once()
        written = transport.write.call_args[0][0]
        from evennia.server.launcher_ipc import _decode_frames

        frames, _ = _decode_frames(bytearray(written))
        self.assertEqual(frames[0]["type"], "status")
        self.assertEqual(frames[0]["status"][2], 99)

    @patch("evennia.server.portal.launcher_handlers.receive_launcher_command")
    @patch("evennia.server.launcher_ipc.clock.get_bound_loop")
    def test_dispatch_command_frame(self, mock_get_loop, mock_receive):
        from evennia.server.launcher_ipc import _LauncherIPCProtocol, _encode_frame

        loop = asyncio.new_event_loop()
        mock_get_loop.return_value = loop
        protocol = MagicMock()
        factory = MagicMock()
        ipc = _LauncherIPCProtocol(MagicMock(), factory, protocol)
        transport = MagicMock()
        ipc.connection_made(transport)
        ipc.data_received(_encode_frame({"type": "command", "operation": "\x0f", "arguments": ""}))
        transport.write.assert_called()
        mock_receive.assert_called_once()
        self.assertIs(factory.launcher_connection, ipc._link)
        loop.close()
