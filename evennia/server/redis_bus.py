"""Redis-Streams Portal<->Server bus, an alternative to AMP.

Gated by ``settings.SERVER_PORTAL_BUS`` ("amp" default | "redis"). It reuses the
AMP protocol classes for their (battle-tested) message packing and receive/
dispatch logic, but replaces the wire: instead of AMP ``callRemote`` over a TCP
socket, frames are published to redis streams; a reader thread consumes the
opposite stream and feeds frames back into the existing responder methods.

Why redis Streams (not AMP, not unix+msgpack): the streams + consumer-group
model lets sessions fan out across multiple Server workers (horizontal scale),
decouples Portal and Server, and survives a worker reload (the stream keeps
in-flight frames). v1 keeps the 1:1 topology (single Portal, single Server) with
worker-keyed stream names so multi-worker is a later config change.

Threading: a writer thread drains a publish queue (XADD) and a reader thread
blocks on XREAD; both hand off to the reactor via ``reactor.callFromThread`` so
game state is only ever touched on the reactor thread, matching the AMP model.

Reload note: readers start at "$" (new frames only), matching AMP's behaviour
that frames sent while the Server is down are not replayed; the PSYNC handshake
re-establishes session state. Durable replay (consumer groups) is a later step.

Verified: gated off by default; the live redis path is prod-verified (dev has no
redis).
"""

import os
import queue
import threading

from django.conf import settings
from twisted.internet import defer

from evennia.server.portal import amp
from evennia.utils import clock, logger

# Frame field names in the redis stream entries.
_CMD = b"c"
_DATA = b"d"


def _bus_url():
    return getattr(settings, "REDIS_BUS_URL", "redis://127.0.0.1:6379/1")


def _stream_prefix():
    return getattr(settings, "REDIS_BUS_PREFIX", "evennia:bus")


def _worker_id():
    # Single-worker for now; multi-worker will assign distinct ids per Server.
    return str(getattr(settings, "SERVER_WORKER_ID", "0"))


class _RedisTransport:
    """Shared redis stream transport: a writer thread + a reader thread."""

    def __init__(self, read_stream, on_frame):
        self._url = _bus_url()
        self._read_stream = read_stream
        self._on_frame = on_frame  # called on the reactor thread: (cmdkey_bytes, data_bytes)
        self._client = None
        self._q = queue.Queue()
        self._writer = None
        self._reader = None
        self._stop = threading.Event()

    def start(self):
        import redis

        self._client = redis.Redis.from_url(self._url)
        self._client.ping()  # fail fast if redis is unreachable
        self._stop.clear()
        self._writer = threading.Thread(target=self._writer_loop, name="redis-bus-writer", daemon=True)
        self._reader = threading.Thread(target=self._reader_loop, name="redis-bus-reader", daemon=True)
        self._writer.start()
        self._reader.start()

    def stop(self):
        self._stop.set()
        self._q.put(None)  # unblock the writer
        for t in (self._writer, self._reader):
            if t is not None:
                t.join(timeout=3)
        self._writer = self._reader = None
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass

    def publish(self, stream, cmdkey, data):
        """Queue a frame for the writer thread (never blocks the reactor)."""
        self._q.put((stream, cmdkey, data))

    def _writer_loop(self):
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                break
            stream, cmdkey, data = item
            try:
                self._client.xadd(
                    stream,
                    {_CMD: cmdkey, _DATA: data if data is not None else b""},
                    maxlen=10000,
                    approximate=True,
                )
            except Exception:
                logger.log_trace("redis bus: publish failed")

    def _reader_loop(self):
        last_id = b"$"  # only new frames
        while not self._stop.is_set():
            try:
                resp = self._client.xread({self._read_stream: last_id}, count=64, block=1000)
            except Exception:
                if self._stop.is_set():
                    break
                logger.log_trace("redis bus: xread failed")
                continue
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id
                    cmdkey = fields.get(_CMD, b"")
                    data = fields.get(_DATA, b"")
                    try:
                        clock.call_from_thread(self._on_frame, cmdkey, data)
                    except Exception:
                        logger.log_trace("redis bus: dispatch failed")


class _RedisBusMixin:
    """Common facade: override AMP's callRemote to publish; dispatch reads."""

    #: subclasses set these
    _send_stream = ""   # stream we publish to
    _read_stream = ""   # stream we consume

    def _init_bus(self, shim_factory):
        self.factory = shim_factory
        self._transport = _RedisTransport(self._read_stream, self._on_frame)

    # -- outbound: replace the AMP wire with a stream publish ------------
    def callRemote(self, command, **kwargs):
        cmdkey = command.key
        if isinstance(cmdkey, str):
            cmdkey = cmdkey.encode()
        self._transport.publish(self._send_stream, cmdkey, kwargs.get("packed_data"))
        return defer.succeed(None)

    # -- inbound: route a stream frame to the matching AMP responder -----
    def _on_frame(self, cmdkey, data):
        raise NotImplementedError

    def start_bus(self):
        self._transport.start()

    def stop_bus(self):
        try:
            self._transport.stop()
        except Exception:
            logger.log_trace("redis bus: stop")


class _ServerShimFactory:
    def __init__(self, server):
        self.server = server


class _PortalShimFactory:
    def __init__(self, portal):
        self.portal = portal
        self.server_connection = None
        self.server_connect_callbacks = []
        self.launcher_connection = None
        self.broadcasts = []
        self.disconnect_callbacks = {}


def _pid_alive(pid):
    """Is the process with this pid still running? (redis-native liveness)."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)  # POSIX: raises if the process is gone
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    except (OSError, ValueError, TypeError):
        # Windows fallback (dev doesn't use redis mode, but be safe).
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, 0, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True


class _PidTransport:
    """Stands in for a Twisted transport: 'connected' == the Server pid is alive.

    Lets the AMP lifecycle code (get_status, wait_for_disconnect) treat the redis
    bus as if it were the AMP server connection.
    """

    def __init__(self, portal):
        self._portal = portal

    @property
    def connected(self):
        return _pid_alive(getattr(self._portal, "server_process_id", None))


class RedisServerBus(_RedisBusMixin, amp.AMPMultiConnectionProtocol):
    """Server-side bus. Reuses AMPServerClientProtocol's send/receive logic."""

    def __init__(self, server):
        amp.AMPMultiConnectionProtocol.__init__(self)
        w = _worker_id()
        prefix = _stream_prefix()
        self._send_stream = f"{prefix}:s2p"
        self._read_stream = f"{prefix}:p2s:{w}"
        self._init_bus(_ServerShimFactory(server))

    def _on_frame(self, cmdkey, data):
        from evennia.server.amp_client import AMPServerClientProtocol

        if cmdkey == b"MsgPortal2Server":
            AMPServerClientProtocol.server_receive_msgportal2server(self, data)
        elif cmdkey == b"AdminPortal2Server":
            AMPServerClientProtocol.server_receive_adminportal2server(self, data)

    def send_MsgServer2Portal(self, session, **kwargs):
        from evennia.server.amp_client import AMPServerClientProtocol

        return AMPServerClientProtocol.send_MsgServer2Portal(self, session, **kwargs)

    def send_AdminServer2Portal(self, session, operation="", **kwargs):
        from evennia.server.amp_client import AMPServerClientProtocol

        return AMPServerClientProtocol.send_AdminServer2Portal(self, session, operation=operation, **kwargs)

    def data_to_portal(self, command, sessid, **kwargs):
        from evennia.server.amp_client import AMPServerClientProtocol

        return AMPServerClientProtocol.data_to_portal(self, command, sessid, **kwargs)

    def start_bus(self):
        _RedisBusMixin.start_bus(self)
        # AMP does this on connectionMade: ask the Portal to resync all sessions.
        info_dict = self.factory.server.get_info_dict()
        self.send_AdminServer2Portal(amp.DUMMYSESSION, operation=amp.PSYNC, spid=os.getpid(), info_dict=info_dict)
        self.factory.server.run_initial_setup()


class RedisPortalBus(_RedisBusMixin, amp.AMPMultiConnectionProtocol):
    """Portal-side bus. Reuses AMPServerProtocol's send/receive logic."""

    def __init__(self, portal, factory=None):
        amp.AMPMultiConnectionProtocol.__init__(self)
        w = _worker_id()
        prefix = _stream_prefix()
        self._send_stream = f"{prefix}:p2s:{w}"
        self._read_stream = f"{prefix}:s2p"
        self._portal = portal
        # Share the AMP server's factory when given, so the launcher-control code
        # (get_status, wait_for_server_connect, server_connect_callbacks) and this
        # bus agree on a single server_connection / callback list.
        self._init_bus(factory if factory is not None else _PortalShimFactory(portal))
        # Make the AMP lifecycle code treat us as the server connection: a
        # pid-backed transport stands in for the TCP link that redis doesn't have.
        self.transport = _PidTransport(portal)

    def _on_frame(self, cmdkey, data):
        from evennia.server.portal.amp_server import AMPServerProtocol

        if cmdkey == b"MsgServer2Portal":
            AMPServerProtocol.portal_receive_server2portal(self, data)
        elif cmdkey == b"AdminServer2Portal":
            AMPServerProtocol.portal_receive_adminserver2portal(self, data)

    def send_MsgPortal2Server(self, session, **kwargs):
        from evennia.server.portal.amp_server import AMPServerProtocol

        return AMPServerProtocol.send_MsgPortal2Server(self, session, **kwargs)

    def send_AdminPortal2Server(self, session, operation="", **kwargs):
        from evennia.server.portal.amp_server import AMPServerProtocol

        return AMPServerProtocol.send_AdminPortal2Server(self, session, operation=operation, **kwargs)

    def data_to_server(self, command, sessid, **kwargs):
        # Pack + publish directly (no AMP connection-tracking/broadcast).
        if command in (amp.AdminPortal2Server,):
            packed = amp.dumps_admin((sessid, kwargs))
        else:
            packed = amp.dumps_session((sessid, kwargs))
        return self.callRemote(command, packed_data=packed)

    def stop_server(self, mode="shutdown"):
        from evennia.server.portal.amp_server import AMPServerProtocol

        return AMPServerProtocol.stop_server(self, mode=mode)

    def start_server(self, server_twistd_cmd):
        # (Re)spawn the Server process. Reused verbatim from AMP: it Popens the
        # twistd command and records server_process_id on the portal.
        from evennia.server.portal.amp_server import AMPServerProtocol

        return AMPServerProtocol.start_server(self, server_twistd_cmd)

    def wait_for_disconnect(self, callback, *args, **kwargs):
        """Fire ``callback`` once the Server process has exited.

        AMP fires this off the TCP connection dropping; over redis there is no
        such socket, so we poll the Server pid (which the Portal recorded at
        PSYNC / when it spawned the process) until it is gone.
        """

        def _poll():
            if not self.transport.connected:
                try:
                    callback(*args, **kwargs)
                except Exception:
                    logger.log_trace("redis bus: wait_for_disconnect callback failed")
            else:
                clock.call_later(0.2, _poll)

        clock.call_later(0.2, _poll)
