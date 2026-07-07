"""Asyncio launcher ↔ Portal control plane (T3 S10).

Replaces Twisted AMP TCP on ``AMP_PORT`` with length-prefixed JSON frames over
the same host/port. Uses :mod:`evennia.server.amp_serde` ``S1``/``L1`` payloads
for status and start-command args.

Frame format: 4-byte big-endian length + UTF-8 JSON body.

Request types (launcher → Portal):
  ``{"type": "status"}``
  ``{"type": "command", "operation": "<char>", "arguments": "<base64 L1 wire>"}``

Response types (Portal → launcher):
  ``{"type": "status", "status": <6-tuple list>}``  — immediate status query
  ``{"type": "ack"}``  — command accepted; a ``status_push`` may follow
  ``{"type": "status_push", "status": <6-tuple list>}``  — async status update
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import struct
from typing import Any

from evennia.utils import clock, logger


def _encode_frame(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(body)) + body


def _decode_frames(buffer: bytearray) -> tuple[list[dict], bytearray]:
    frames = []
    while len(buffer) >= 4:
        (size,) = struct.unpack("!I", buffer[:4])
        if len(buffer) < 4 + size:
            break
        chunk = bytes(buffer[4 : 4 + size])
        del buffer[: 4 + size]
        frames.append(json.loads(chunk.decode("utf-8")))
    return frames, buffer


class LauncherIPCConnection:
    """Active launcher control connection (Portal side)."""

    def __init__(self, transport, loop: asyncio.AbstractEventLoop):
        self._transport = transport
        self._loop = loop

    def push_status(self, status_tuple):
        """Send an async status push to the launcher (replaces AMP MsgStatus push)."""
        payload = {"type": "status_push", "status": list(status_tuple)}

        def _write():
            try:
                self._transport.write(_encode_frame(payload))
            except Exception:
                logger.log_trace("launcher IPC: status push failed")

        self._loop.call_soon_threadsafe(_write)


class _LauncherIPCProtocol(asyncio.Protocol):
    def __init__(self, portal, amp_factory, amp_protocol):
        self._portal = portal
        self._factory = amp_factory
        self._protocol = amp_protocol
        self._buffer = bytearray()
        self._link = None
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        peer = transport.get_extra_info("peername")
        host = peer[0] if peer else "?"
        logger.log_info(f"Launcher IPC connected from {host}")

    def data_received(self, data):
        self._buffer.extend(data)
        frames, self._buffer = _decode_frames(self._buffer)
        for frame in frames:
            self._dispatch(frame)

    def connection_lost(self, exc):
        if self._link is not None and self._factory.launcher_connection is self._link:
            self._factory.launcher_connection = None

    def _dispatch(self, frame):
        from evennia.server.portal import launcher_handlers

        ftype = frame.get("type")
        if ftype == "status":
            status = self._protocol.get_status()
            self._write({"type": "status", "status": list(status)})
            return
        if ftype == "command":
            operation = frame.get("operation", "")
            args_b64 = frame.get("arguments") or ""
            args_wire = base64.b64decode(args_b64) if args_b64 else b""
            loop = clock.get_bound_loop()
            self._link = LauncherIPCConnection(self.transport, loop)
            self._factory.launcher_connection = self._link
            self._write({"type": "ack"})
            launcher_handlers.receive_launcher_command(
                self._protocol, operation, args_wire
            )
            return
        logger.log_err(f"launcher IPC: unknown frame type {ftype!r}")

    def _write(self, payload: dict):
        try:
            self.transport.write(_encode_frame(payload))
        except Exception:
            logger.log_trace("launcher IPC: write failed")


class LauncherSession:
    """Blocking launcher client for one control session (replaces AMP launcher)."""

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = bytearray()

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _read_frame(self) -> dict:
        assert self._sock is not None
        while True:
            frames, self._buffer = _decode_frames(self._buffer)
            if frames:
                return frames[0]
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("launcher IPC connection closed")
            self._buffer.extend(chunk)

    def _send(self, payload: dict):
        assert self._sock is not None
        self._sock.sendall(_encode_frame(payload))

    def query_status(self) -> list:
        self._send({"type": "status"})
        resp = self._read_frame()
        if resp.get("type") != "status":
            raise RuntimeError(f"unexpected launcher IPC response: {resp!r}")
        return resp["status"]

    def send_command_fire(self, operation: str, arguments: Any):
        """Send a launcher command and read the immediate ack (not the status push)."""
        from evennia.server.amp_serde import pack_launcher_args

        args_wire = pack_launcher_args(arguments) if arguments is not None else b""
        self._send(
            {
                "type": "command",
                "operation": operation,
                "arguments": base64.b64encode(args_wire).decode("ascii") if args_wire else "",
            }
        )
        ack = self._read_frame()
        if ack.get("type") != "ack":
            raise RuntimeError(f"unexpected launcher IPC ack: {ack!r}")

    def read_push(self, timeout: float | None = None) -> list | None:
        if self._sock is not None and timeout is not None:
            self._sock.settimeout(timeout)
        try:
            push = self._read_frame()
        except socket.timeout:
            return None
        if push.get("type") == "status_push":
            return push["status"]
        return None


_servers: list = []


async def _start_server(portal, amp_factory, amp_protocol, interface: str, port: int):
    global _servers

    def _factory():
        return _LauncherIPCProtocol(portal, amp_factory, amp_protocol)

    server = await asyncio.start_server(_factory, interface, port)
    _servers.append(server)
    portal.info_dict["amp"] = f"launcher-ipc: {port}"
    logger.log_info(f"Launcher IPC listening on {interface}:{port}")


def start_launcher_server(portal, amp_factory, amp_protocol, interface: str, port: int):
    """Schedule the asyncio launcher control server on the bound process loop."""
    loop = clock.get_bound_loop()

    async def _go():
        try:
            await _start_server(portal, amp_factory, amp_protocol, interface, port)
        except Exception:
            logger.log_trace("launcher IPC server failed to start")

    loop.create_task(_go())


async def stop_launcher_servers():
    global _servers
    for server in _servers:
        server.close()
        await server.wait_closed()
    _servers = []
