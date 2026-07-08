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

Trust boundary: the control plane is UNAUTHENTICATED. A ``command`` frame can
start/stop the Server, so any process that can open ``AMP_HOST:AMP_PORT`` gains
that control. Security rests entirely on binding a loopback interface: keep
``AMP_HOST`` at ``127.0.0.1`` (the default). Do not expose this port on a routable
interface without adding an auth layer first.
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

# Generous ceiling on a single frame body. Real status/command frames are tiny;
# a larger declared size is a corrupt or hostile peer, so we refuse to buffer it.
_MAX_FRAME_SIZE = 8 * 1024 * 1024


class LauncherIPCFrameError(Exception):
    """Unrecoverable launcher IPC frame-layer violation.

    Raised for an oversize declared frame length or a body that cannot be
    decoded as JSON. Both mean the byte stream is corrupt past the point of
    recovery, so the caller should drop the connection rather than resync.
    """


def _encode_frame(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(body)) + body


def _decode_frames(buffer: bytearray) -> tuple[list[dict], bytearray]:
    """Decode as many whole frames as *buffer* holds, returning the remainder.

    Args:
        buffer (bytearray): Accumulated inbound bytes, consumed in place.

    Returns:
        tuple: ``(frames, remainder)`` where *frames* is a list of decoded
        payload dicts and *remainder* is the trailing bytes of an incomplete
        frame (may be empty).

    Raises:
        LauncherIPCFrameError: A declared frame length exceeds
            :data:`_MAX_FRAME_SIZE`, or a complete body is not valid JSON.
    """
    frames = []
    while len(buffer) >= 4:
        (size,) = struct.unpack("!I", buffer[:4])
        if size > _MAX_FRAME_SIZE:
            raise LauncherIPCFrameError(
                f"launcher IPC frame size {size} exceeds cap {_MAX_FRAME_SIZE}"
            )
        if len(buffer) < 4 + size:
            break
        chunk = bytes(buffer[4 : 4 + size])
        del buffer[: 4 + size]
        try:
            frames.append(json.loads(chunk.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LauncherIPCFrameError("launcher IPC frame body is not valid JSON") from exc
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
        try:
            frames, self._buffer = _decode_frames(self._buffer)
        except LauncherIPCFrameError:
            logger.log_trace("launcher IPC: corrupt frame stream, closing connection")
            self.transport.close()
            return
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
            try:
                args_wire = base64.b64decode(args_b64) if args_b64 else b""
            except (ValueError, TypeError):
                # Drop just this frame; a bad-base64 argument is not a reason to
                # tear down an otherwise healthy control channel.
                logger.log_trace("launcher IPC: bad base64 command arguments, dropping frame")
                return
            if self._link is None:
                loop = clock.get_bound_loop()
                self._link = LauncherIPCConnection(self.transport, loop)
                self._factory.launcher_connection = self._link
            self._write({"type": "ack"})
            launcher_handlers.receive_launcher_command(self._protocol, operation, args_wire)
            return
        logger.log_err(f"launcher IPC: unknown frame type {ftype!r}")

    def _write(self, payload: dict):
        try:
            self.transport.write(_encode_frame(payload))
        except Exception:
            logger.log_trace("launcher IPC: write failed")


COLD_START_DEADLINE = 120.0
# Shutdown waits: portal teardown can outlast a short status poll.
SHUTDOWN_WAIT_DEADLINE = 60.0
# Quick probe when checking if Portal IPC is already listening (not cold-start wait).
PSTATUS_PROBE_TIMEOUT = 5.0


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
            try:
                frames, self._buffer = _decode_frames(self._buffer)
            except LauncherIPCFrameError as exc:
                logger.log_trace("launcher IPC: corrupt frame stream on read")
                raise ConnectionError("launcher IPC frame error") from exc
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
        while True:
            resp = self._read_frame()
            ftype = resp.get("type")
            if ftype == "status":
                return resp["status"]
            if ftype == "status_push":
                # Portal may push status on connect before answering the query.
                continue
            raise RuntimeError(f"unexpected launcher IPC response: {resp!r}")

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
        while True:
            ack = self._read_frame()
            if ack.get("type") == "ack":
                return
            if ack.get("type") == "status_push":
                continue
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
        # A non-status_push frame here (e.g. the ``ack`` that precedes a push) is
        # intentionally skipped, not an error: the full frame was already consumed
        # so framing stays aligned, and the caller re-reads for the next push.
        return None

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
            if push is not None and self._state_matches(push, portal_running, server_running):
                return push
        return None


def portal_ipc_reachable(host: str, port: int, *, timeout: float = 2.0) -> bool:
    """Return True when launcher IPC accepts a TCP connection."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (OSError, ConnectionError, TimeoutError):
        return False
    try:
        sock.close()
    except OSError:
        pass
    return True


def wait_for_portal_ipc_down(
    host: str,
    port: int,
    *,
    deadline: float = 15.0,
    poll_interval: float = 0.2,
) -> bool:
    """Block until launcher IPC is not accepting connections."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if not portal_ipc_reachable(host, port, timeout=min(2.0, poll_interval)):
            return True
        time.sleep(poll_interval)
    return not portal_ipc_reachable(host, port, timeout=2.0)


def query_ipc_status(host: str, port: int, *, timeout: float = 5.0) -> list | None:
    """One-shot launcher IPC status query (connect, ask, close)."""
    session = LauncherSession(host, port, timeout=timeout)
    try:
        session.connect()
        return session.query_status()
    except (TimeoutError, OSError, ConnectionError, RuntimeError):
        return None
    finally:
        session.close()


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
            if portal_running is False:
                # Portal IPC listener is gone — portal has exited.
                return [False, False, None, None, {}, {}]
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
