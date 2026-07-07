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
import time
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
        loop = clock.get_bound_loop()
        self._link = LauncherIPCConnection(self.transport, loop)
        self._factory.launcher_connection = self._link
        try:
            self._protocol.send_Status2Launcher()
        except Exception:
            logger.log_trace("launcher IPC: connect status push failed")

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
            if self._link is None:
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


COLD_START_DEADLINE = 120.0


def connect_session(
    host: str,
    port: int,
    *,
    timeout: float = 30.0,
    connect_timeout: float = COLD_START_DEADLINE,
    retry_interval: float = 0.5,
) -> LauncherSession:
    """Connect to Portal launcher IPC, retrying until timeout (cold-start safe)."""
    session = LauncherSession(host, port, timeout=timeout)
    deadline = time.monotonic() + connect_timeout
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            session.connect()
            return session
        except (OSError, ConnectionError, TimeoutError) as err:
            last_err = err
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(retry_interval, remaining))
    raise ConnectionError(
        f"launcher IPC connect to {host}:{port} timed out after {connect_timeout}s"
    ) from last_err


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

    def wait_for_push(self, timeout: float = 120.0) -> list | None:
        """Block until a ``status_push`` frame arrives or timeout."""
        return self.read_push(timeout=timeout)

    def _state_matches(
        self,
        status: list,
        portal_running,
        server_running,
    ) -> bool:
        from evennia.server.redis_bus import _pid_alive

        prun, srun, ppid, spid, _, _ = status
        if portal_running is False and ppid and not _pid_alive(ppid):
            prun = False
        if server_running is False and spid and not _pid_alive(spid):
            srun = False
        if portal_running is not None and prun != portal_running:
            return False
        if server_running is not None and srun != server_running:
            return False
        return True

    def wait_for_state(
        self,
        portal_running=True,
        server_running=True,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> list | None:
        """Block until status matches desired portal/server run-state."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self.query_status()
            except (TimeoutError, OSError, ConnectionError):
                time.sleep(min(poll_interval, deadline - time.monotonic()))
                continue
            if self._state_matches(status, portal_running, server_running):
                return status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            push = self.read_push(timeout=min(remaining, poll_interval))
            if push is not None and self._state_matches(
                push, portal_running, server_running
            ):
                return push
        return None


def wait_until_state(
    host: str,
    port: int,
    *,
    portal_running=True,
    server_running=True,
    deadline: float = COLD_START_DEADLINE,
    poll_interval: float = 0.5,
) -> list | None:
    """Retry connect + status query until state matches or *deadline* elapses."""
    end = time.monotonic() + deadline
    session = LauncherSession(host, port)
    while time.monotonic() < end:
        remaining = end - time.monotonic()
        try:
            session.connect()
        except (OSError, ConnectionError, TimeoutError):
            session.close()
            session = LauncherSession(host, port)
            time.sleep(min(poll_interval, remaining))
            continue
        try:
            status = session.wait_for_state(
                portal_running=portal_running,
                server_running=server_running,
                timeout=min(poll_interval, remaining),
                poll_interval=poll_interval,
            )
            if status is not None:
                return status
        except (TimeoutError, OSError, ConnectionError, RuntimeError):
            pass
        session.close()
        session = LauncherSession(host, port)
        time.sleep(min(poll_interval, remaining))
    return None


_servers: list = []


async def _start_server(portal, amp_factory, amp_protocol, interface: str, port: int):
    global _servers
    loop = clock.get_bound_loop()

    def protocol_factory():
        return _LauncherIPCProtocol(portal, amp_factory, amp_protocol)

    server = await loop.create_server(protocol_factory, interface, port)
    _servers.append(server)
    portal._launcher_ipc_ready = True
    portal.info_dict["amp"] = f"launcher-ipc: {port}"
    logger.log_info(f"Launcher IPC listening on {interface}:{port}")
    protocol = getattr(portal, "_launcher_amp_protocol", None)
    if protocol is not None:
        try:
            protocol.send_Status2Launcher()
        except Exception:
            logger.log_trace("launcher IPC: ready status push failed")


def start_launcher_server(portal, amp_factory, amp_protocol, interface: str, port: int):
    """Start launcher IPC on the bound process loop.

    Binds synchronously when the loop is not yet running (portal cold boot),
    matching legacy Twisted ``TCPServer`` behaviour so the launcher can connect
    before telnet/web/plugins finish initializing.
    """
    loop = clock.get_bound_loop()
    if loop is None:
        logger.log_err("Launcher IPC requires a bound asyncio loop.")
        return

    if loop.is_running():
        async def _go():
            try:
                await _start_server(portal, amp_factory, amp_protocol, interface, port)
            except Exception:
                logger.log_trace("launcher IPC server failed to start")

        loop.create_task(_go())
        return

    try:
        loop.run_until_complete(_start_server(portal, amp_factory, amp_protocol, interface, port))
    except Exception:
        logger.log_trace("launcher IPC server failed to start")


async def stop_launcher_servers():
    global _servers
    for server in _servers:
        server.close()
        await server.wait_closed()
    _servers = []
