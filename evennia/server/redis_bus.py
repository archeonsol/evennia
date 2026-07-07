"""Redis-Streams Portal<->Server bus (plain XADD/XREAD; no consumer groups).

Gated by ``settings.SERVER_PORTAL_BUS`` (must be ``"redis"``). Session/admin
frames are published to redis streams; reader threads block on ``XREAD`` and
dispatch into :mod:`evennia.server.ipc_handlers_server` /
:mod:`evennia.server.portal.ipc_handlers_portal` on the reactor thread.

The Portal AMP TCP listener on ``AMP_PORT`` remains for launcher control only.

Threading: writer/reader threads hand off via ``clock.call_from_thread`` so game
state is only touched on the reactor thread.

Reload: readers start at ``$`` (new frames only); PSYNC re-establishes session
state. Durable replay (consumer groups) is a later step.
"""

import os
import queue
import threading

from django.conf import settings
from twisted.internet import defer

from evennia.server import ipc_handlers_server
from evennia.server.portal import amp
from evennia.server.portal import ipc_handlers_portal
from evennia.utils import clock, logger

# Frame field names in the redis stream entries.
_CMD = b"c"
_DATA = b"d"


def _bus_url():
    return getattr(settings, "REDIS_BUS_URL", "redis://127.0.0.1:6379/1")


def _stream_prefix():
    return getattr(settings, "REDIS_BUS_PREFIX", "evennia:bus")


def _worker_id():
    return str(getattr(settings, "SERVER_WORKER_ID", "0"))


class _RedisTransport:
    """Shared redis stream transport: a writer thread + a reader thread."""

    def __init__(self, read_stream, on_frame):
        self._url = _bus_url()
        self._read_stream = read_stream
        self._on_frame = on_frame
        self._client = None
        self._q = queue.Queue()
        self._writer = None
        self._reader = None
        self._stop = threading.Event()

    def start(self):
        import redis

        self._client = redis.Redis.from_url(self._url)
        self._client.ping()
        self._stop.clear()
        self._writer = threading.Thread(target=self._writer_loop, name="redis-bus-writer", daemon=True)
        self._reader = threading.Thread(target=self._reader_loop, name="redis-bus-reader", daemon=True)
        self._writer.start()
        self._reader.start()

    def stop(self):
        self._stop.set()
        self._q.put(None)
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
        last_id = b"$"
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
    _send_stream = ""
    _read_stream = ""

    def _init_bus(self, shim_factory):
        self.factory = shim_factory
        self._transport = _RedisTransport(self._read_stream, self._on_frame)

    def callRemote(self, command, **kwargs):
        cmdkey = command.key
        if isinstance(cmdkey, str):
            cmdkey = cmdkey.encode()
        self._transport.publish(self._send_stream, cmdkey, kwargs.get("packed_data"))
        return defer.succeed(None)

    def _on_frame(self, cmdkey, data):
        raise NotImplementedError

    def start_bus(self):
        self._transport.start()

    def stop_bus(self):
        try:
            self._transport.stop()
        except Exception:
            logger.log_trace("redis bus: stop")

    def errback(self, err, info):
        logger.log_trace("redis bus errback (%s): %s" % (info, err))


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
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
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
    def __init__(self, portal):
        self._portal = portal

    @property
    def connected(self):
        return _pid_alive(getattr(self._portal, "server_process_id", None))


class RedisServerBus(_RedisBusMixin):
    def __init__(self, server):
        w = _worker_id()
        prefix = _stream_prefix()
        self._send_stream = f"{prefix}:s2p"
        self._read_stream = f"{prefix}:p2s:{w}"
        self._init_bus(_ServerShimFactory(server))

    def _on_frame(self, cmdkey, data):
        if cmdkey == b"MsgPortal2Server":
            ipc_handlers_server.receive_msgportal2server(data)
        elif cmdkey == b"AdminPortal2Server":
            ipc_handlers_server.receive_adminportal2server(data)

    def send_MsgServer2Portal(self, session, **kwargs):
        return ipc_handlers_server.send_msgserver2portal(self, session, **kwargs)

    def send_AdminServer2Portal(self, session, operation="", **kwargs):
        return ipc_handlers_server.send_adminserver2portal(
            self, session, operation=operation, **kwargs
        )

    def data_to_portal(self, command, sessid, **kwargs):
        return ipc_handlers_server.data_to_portal(self, command, sessid, **kwargs)

    def start_bus(self):
        _RedisBusMixin.start_bus(self)
        info_dict = self.factory.server.get_info_dict()
        self.send_AdminServer2Portal(
            amp.DUMMYSESSION, operation=amp.PSYNC, spid=os.getpid(), info_dict=info_dict
        )
        self.factory.server.run_initial_setup()


class RedisPortalBus(_RedisBusMixin):
    def __init__(self, portal, factory=None):
        w = _worker_id()
        prefix = _stream_prefix()
        self._send_stream = f"{prefix}:p2s:{w}"
        self._read_stream = f"{prefix}:s2p"
        self._portal = portal
        self._init_bus(factory if factory is not None else _PortalShimFactory(portal))
        self.transport = _PidTransport(portal)

    def _on_frame(self, cmdkey, data):
        if cmdkey == b"MsgServer2Portal":
            ipc_handlers_portal.receive_server2portal(data)
        elif cmdkey == b"AdminServer2Portal":
            ipc_handlers_portal.receive_adminserver2portal(self, data)

    def send_MsgPortal2Server(self, session, **kwargs):
        return ipc_handlers_portal.send_msgportal2server(self, session, **kwargs)

    def send_AdminPortal2Server(self, session, operation="", **kwargs):
        return ipc_handlers_portal.send_adminportal2server(
            self, session, operation=operation, **kwargs
        )

    def data_to_server(self, command, sessid, **kwargs):
        if command in (amp.AdminPortal2Server,):
            packed = amp.dumps_admin((sessid, kwargs))
        else:
            packed = amp.dumps_session((sessid, kwargs))
        return self.callRemote(command, packed_data=packed)

    def stop_server(self, mode="shutdown"):
        from evennia.server.portal import amp_server

        return amp_server.AMPServerProtocol.stop_server(self, mode=mode)

    def start_server(self, server_twistd_cmd):
        from evennia.server.portal import amp_server

        return amp_server.AMPServerProtocol.start_server(self, server_twistd_cmd)

    def wait_for_disconnect(self, callback, *args, **kwargs):
        def _poll():
            if not self.transport.connected:
                try:
                    callback(*args, **kwargs)
                except Exception:
                    logger.log_trace("redis bus: wait_for_disconnect callback failed")
            else:
                clock.call_later(0.2, _poll)

        clock.call_later(0.2, _poll)
