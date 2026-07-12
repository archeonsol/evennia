"""Redis-Streams Portal<->Server bus (plain XADD/XREAD; no consumer groups).

Gated by ``settings.SERVER_PORTAL_BUS`` (must be ``"redis"``). Session/admin
frames are published to redis streams; reader threads block on ``XREAD`` and
dispatch into :mod:`evennia.server.ipc_handlers_server` /
:mod:`evennia.server.portal.ipc_handlers_portal` on the reactor thread.

The Portal AMP TCP listener on ``AMP_PORT`` remains for launcher control only.

Threading: writer/reader threads hand off via ``clock.call_from_thread`` so game
state is only touched on the reactor thread.

Reload: readers use Redis Streams **consumer groups**, so the read cursor lives
server-side and survives a Server reload. Frames the Portal wrote while the
Server was down are delivered on restart (no ``$`` skip), and a consumer's
un-acked (pending) frames from a crash are reclaimed on the next start before new
frames are read. The writer retries a failed ``XADD`` and never silently drops a
control frame. PSYNC still re-establishes session metadata on top of this.
"""

import os
import queue
import threading
import time

import psutil
from django.conf import settings

from evennia.server import ipc_handlers_server
from evennia.server.portal import amp, ipc_handlers_portal
from evennia.server.service_registry import IMMEDIATE_RESULT
from evennia.utils import clock, logger

# Frame field names in the redis stream entries.
_CMD = b"c"
_DATA = b"d"

# Cap the outbound publish queue so a stalled/unreachable redis (writer thread
# blocked in xadd) cannot grow it without bound and OOM the process. On
# overflow the newest frame is dropped (best-effort delivery is the bus
# contract); one log per stall episode keeps a real outage visible without
# flooding.
_MAX_QUEUE = 10000


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
        self._q = queue.Queue(maxsize=_MAX_QUEUE)
        self._queue_full_logged = False
        self._writer = None
        self._reader = None
        self._stop = threading.Event()
        # Consumer-group identity for the read stream. The group's last-delivered
        # cursor lives on the redis server, so it survives a Server reload — that
        # is what stops the old ``$`` reader from skipping frames written during
        # downtime.
        self._group = f"{read_stream}:grp"
        self._consumer = f"{_worker_id()}-{os.getpid()}"

    def _ensure_group(self):
        """Create the consumer group if absent. New group starts at ``0`` so a
        first-ever boot reads from the start of the retained stream rather than
        skipping; an existing group keeps its durable cursor (BUSYGROUP)."""
        import redis

        try:
            self._client.xgroup_create(self._read_stream, self._group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as err:
            if "BUSYGROUP" not in str(err):
                logger.log_trace("redis bus: xgroup_create failed")
        except Exception:
            logger.log_trace("redis bus: xgroup_create error")

    def start(self):
        if self._reader is not None and self._reader.is_alive():
            return
        import redis

        self._client = redis.Redis.from_url(self._url)
        try:
            self._client.ping()
        except redis.exceptions.RedisError as err:
            # The redis bus is the sole Portal<->Server transport; a Server that
            # cannot reach it on boot has no way to serve. Fail fast: log clearly
            # and stop the loop so the process exits and the launcher watchdog
            # retries at process level (throttled) rather than half-initializing.
            logger.log_err(
                "redis bus: cannot reach redis at %s on boot (%s); stopping the "
                "process for launcher retry." % (self._url, err)
            )
            clock.stop_loop()
            return
        self._ensure_group()
        self._stop.clear()
        self._writer = threading.Thread(
            target=self._writer_loop, name="redis-bus-writer", daemon=True
        )
        self._reader = threading.Thread(
            target=self._reader_loop, name="redis-bus-reader", daemon=True
        )
        self._writer.start()
        self._reader.start()

    def stop(self):
        self._stop.set()
        self._q.put(None)
        for t in (self._writer, self._reader):
            if t is not None:
                t.join(timeout=3)
        # Only drop handles for threads that actually exited. A thread wedged in
        # a blocking redis call ignores _stop and outlives the join; keeping its
        # handle lets a later start() see it via is_alive() and skip spawning a
        # duplicate rather than orphaning a live reader/writer.
        if self._writer is not None and self._writer.is_alive():
            logger.log_warn("redis bus: writer thread did not stop within 3s; leaving it running")
        else:
            self._writer = None
        if self._reader is not None and self._reader.is_alive():
            logger.log_warn("redis bus: reader thread did not stop within 3s; leaving it running")
        else:
            self._reader = None
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass

    def publish(self, stream, cmdkey, data):
        try:
            self._q.put_nowait((stream, cmdkey, data))
            self._queue_full_logged = False
        except queue.Full:
            if not self._queue_full_logged:
                self._queue_full_logged = True
                logger.log_err(
                    "redis bus: publish queue full (%d); dropping frames until it drains"
                    % _MAX_QUEUE
                )

    @staticmethod
    def _is_control(cmdkey):
        """Admin* frames carry control/administration traffic (session sync,
        reload, shutdown). They must never share the replaceable-output drop
        policy of ordinary data frames."""
        return bool(cmdkey) and bytes(cmdkey).startswith(b"Admin")

    def _xadd_with_retry(self, stream, cmdkey, data, attempts=3):
        """Publish one frame, retrying a transient failure with backoff. True on
        success, False if it could not be written within ``attempts``."""
        for i in range(attempts):
            if self._stop.is_set():
                return False
            try:
                self._client.xadd(
                    stream,
                    {_CMD: cmdkey, _DATA: data if data is not None else b""},
                    maxlen=10000,
                    approximate=True,
                )
                return True
            except Exception:
                logger.log_trace("redis bus: xadd attempt %d failed" % (i + 1))
                time.sleep(min(0.5, 0.05 * (2**i)))
        return False

    def _writer_loop(self):
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                break
            stream, cmdkey, data = item
            if self._xadd_with_retry(stream, cmdkey, data):
                continue
            # Persistent failure: a control frame is not replaceable output — do
            # not silently drop it. Re-queue it (best effort) and escalate; a
            # data frame follows the best-effort contract and is dropped loudly.
            if self._is_control(cmdkey):
                try:
                    self._q.put_nowait((stream, cmdkey, data))
                    logger.log_err(
                        "redis bus: CONTROL frame %r could not be published; re-queued "
                        "for retry (redis unreachable?)." % cmdkey
                    )
                except queue.Full:
                    logger.log_err(
                        "redis bus: CONTROL frame %r dropped — queue full during a redis "
                        "outage. Control traffic may be lost." % cmdkey
                    )
            else:
                logger.log_err("redis bus: data frame %r dropped after retries" % cmdkey)

    def _dispatch(self, entry_id, fields):
        """Hand one stream entry to the reactor and ack it. At-least-once: the
        durable group cursor + pending reclaim guarantee no frame is skipped;
        a rare double-delivery after a crash-between-dispatch-and-ack is
        tolerable for this transport."""
        cmdkey = fields.get(_CMD, b"")
        data = fields.get(_DATA, b"")
        try:
            clock.call_from_thread(self._on_frame, cmdkey, data)
        except Exception:
            logger.log_trace("redis bus: dispatch failed")
        try:
            self._client.xack(self._read_stream, self._group, entry_id)
        except Exception:
            logger.log_trace("redis bus: xack failed")

    def _drain_pending(self):
        """Reclaim this consumer's un-acked frames from a prior crash (delivered
        but never acked), redelivered by reading the group at id ``0``."""
        try:
            resp = self._client.xreadgroup(
                self._group, self._consumer, {self._read_stream: "0"}, count=256
            )
        except Exception:
            logger.log_trace("redis bus: pending reclaim failed")
            return
        for _stream, entries in resp or []:
            for entry_id, fields in entries:
                self._dispatch(entry_id, fields)

    def _reader_loop(self):
        # First redeliver anything this consumer had in-flight before a restart.
        self._drain_pending()
        while not self._stop.is_set():
            try:
                resp = self._client.xreadgroup(
                    self._group,
                    self._consumer,
                    {self._read_stream: ">"},
                    count=64,
                    block=1000,
                )
            except Exception:
                if self._stop.is_set():
                    break
                logger.log_trace("redis bus: xreadgroup failed")
                # a reconnect may have lost the group; re-ensure and retry
                self._ensure_group()
                continue
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._dispatch(entry_id, fields)


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
        return IMMEDIATE_RESULT

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

    def broadcast(self, command, sessid, **kwargs):
        """Publish when no live server AMP shim is attached (redis-only path)."""
        return self.callRemote(command, **kwargs)


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
        return psutil.pid_exists(int(pid))
    except (ValueError, TypeError):
        return False


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
