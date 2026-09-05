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

import evennia
from evennia.server import ipc_handlers_server
from evennia.server.bus_handshake import BusHandshake
from evennia.server.bus_sessions import SessionReconciler
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
_STOP_TIMEOUT = 3.0
_QUEUE_WAIT = 0.1


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
        self._admission_lock = threading.Lock()
        self._cleanup = None
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
        """Start a fresh worker pair, rejecting overlap with prior workers.

        Returns:
            bool: Whether a fresh pair was started.

        Raises:
            RuntimeError: Old workers or cleanup still own the transport.
        """
        writer_alive = self._writer is not None and self._writer.is_alive()
        reader_alive = self._reader is not None and self._reader.is_alive()
        cleanup_alive = self._cleanup is not None and self._cleanup.is_alive()
        if writer_alive or reader_alive or cleanup_alive:
            if writer_alive and reader_alive and not self._stop.is_set() and not cleanup_alive:
                return False
            raise RuntimeError("redis bus: previous workers or cleanup still running")
        import redis

        self._client = redis.Redis.from_url(self._url)
        self._cleanup = None
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
            return False
        self._ensure_group()
        with self._admission_lock:
            if self._stop.is_set():
                self._q = queue.Queue(maxsize=_MAX_QUEUE)
                self._queue_full_logged = False
            self._stop.clear()
        self._writer = threading.Thread(
            target=self._writer_loop, name="redis-bus-writer", daemon=True
        )
        self._reader = threading.Thread(
            target=self._reader_loop, name="redis-bus-reader", daemon=True
        )
        self._writer.start()
        self._reader.start()
        return True

    def _close_after_workers(self, workers, client):
        """Close the captured client only after its captured workers exit."""
        for worker in workers:
            if worker is not None:
                worker.join()
        try:
            if client is not None:
                client.close()
        except Exception:
            logger.log_trace("redis bus: client cleanup failed")

    def stop(self):
        """Abort local transport work within one bounded wait for worker cleanup."""
        deadline = time.monotonic() + _STOP_TIMEOUT
        with self._admission_lock:
            self._stop.set()
        if self._cleanup is None and any(
            item is not None for item in (self._writer, self._reader, self._client)
        ):
            self._cleanup = threading.Thread(
                target=self._close_after_workers,
                args=((self._writer, self._reader), self._client),
                name="redis-bus-cleanup",
                daemon=True,
            )
            self._cleanup.start()
        if self._cleanup is not None:
            self._cleanup.join(timeout=max(0, deadline - time.monotonic()))
        for name in ("_writer", "_reader", "_cleanup"):
            worker = getattr(self, name)
            if worker is not None and worker.is_alive():
                logger.log_warn(
                    f"redis bus: {name[1:]} still running after shutdown wait; restart blocked"
                )
            elif name != "_cleanup":
                setattr(self, name, None)

    def publish(self, stream, cmdkey, data):
        """Admit a frame unless explicitly stopped; retain the overflow policy.

        Raises:
            RuntimeError: The transport has been stopped.
        """
        warn_full = False
        with self._admission_lock:
            if self._stop.is_set():
                raise RuntimeError("redis bus: publication rejected after stop")
            try:
                self._q.put_nowait((stream, cmdkey, data))
                self._queue_full_logged = False
            except queue.Full:
                warn_full = not self._queue_full_logged
                self._queue_full_logged = True
        if warn_full:
            logger.log_err(
                "redis bus: publish queue full (%d); dropping frames until it drains" % _MAX_QUEUE
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
                if self._stop.wait(min(0.5, 0.05 * (2**i))):
                    return False
        return False

    def _writer_loop(self):
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=_QUEUE_WAIT)
            except queue.Empty:
                continue
            if self._stop.is_set() or item is None:
                break
            stream, cmdkey, data = item
            if self._xadd_with_retry(stream, cmdkey, data):
                continue
            if self._stop.is_set():
                break
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
        if self._stop.is_set():
            return
        try:
            resp = self._client.xreadgroup(
                self._group, self._consumer, {self._read_stream: "0"}, count=256
            )
        except Exception:
            logger.log_trace("redis bus: pending reclaim failed")
            return
        for _stream, entries in resp or []:
            for entry_id, fields in entries:
                if self._stop.is_set():
                    return
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
                    if self._stop.is_set():
                        return
                    self._dispatch(entry_id, fields)


class _RedisBusMixin:
    _send_stream = ""
    _read_stream = ""

    def _init_bus(self, shim_factory):
        self.factory = shim_factory
        self._transport = _RedisTransport(self._read_stream, self._receive_frame)
        self._tick_handle = None
        self.last_sync_error = None
        self._handshake = BusHandshake(
            self._role,
            send=self._send_handshake,
            snapshot=self._snapshot,
            apply=self._apply_snapshot,
            state_snapshot=self._state_snapshot,
            apply_state=self._apply_state,
            on_ready=self._ready,
            on_unavailable=self._unavailable,
        )

    @property
    def ready(self):
        """Whether both peers confirmed the current session generation."""
        return self._handshake.state == "ready"

    def _send_handshake(self, frame):
        """Publish fresh state using the typed admin serializer."""
        self._transport.publish(
            self._send_stream,
            b"BusHandshake",
            amp.dumps_admin((0, {"sessiondata": frame})),
        )

    def _receive_frame(self, cmdkey, data):
        """Keep discovery separate from lifecycle and player frames."""
        if cmdkey == b"BusHandshake":
            try:
                self._handshake.receive(amp.loads_admin(data)[1]["sessiondata"])
            except Exception as error:
                self.last_sync_error = error
                self._handshake.disconnect()
                logger.log_trace("redis bus: synchronization failed")
        else:
            self._on_frame(cmdkey, data)

    def _tick(self):
        """Own one cancellable heartbeat timer per running transport."""
        if self._transport._stop.is_set():
            return
        self._handshake.tick()
        self._tick_handle = clock.call_later(0.5, self._tick)

    def _unavailable(self):
        """Close readiness while retaining live socket and runtime state."""

    def _snapshot(self):
        """Return Portal state; unused by the Server role."""
        return 0, {}

    def _apply_snapshot(self, payload):
        """Apply Portal state; unused by the Portal role."""

    def _state_snapshot(self):
        """Return Server state; unused by the Portal role."""
        return {}

    def _apply_state(self, payload):
        """Apply Server state; unused by the Server role."""

    def callRemote(self, command, **kwargs):
        cmdkey = command.key
        if isinstance(cmdkey, str):
            cmdkey = cmdkey.encode()
        self._transport.publish(self._send_stream, cmdkey, kwargs.get("packed_data"))
        return IMMEDIATE_RESULT

    def _on_frame(self, cmdkey, data):
        raise NotImplementedError

    def start_bus(self):
        """Start transport workers and fresh peer discovery once."""
        if self._transport._reader is None or self._transport._stop.is_set():
            self._handshake.disconnect()
        started = self._transport.start()
        if started:
            self._tick()
        return started

    def stop_bus(self):
        """Cancel owned discovery before stopping worker threads."""
        if self._tick_handle is not None:
            self._tick_handle.cancel()
            self._tick_handle = None
        self.last_sync_error = None
        self._handshake.disconnect()
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
    _role = "server"

    def __init__(self, server):
        w = _worker_id()
        prefix = _stream_prefix()
        self._send_stream = f"{prefix}:s2p"
        self._read_stream = f"{prefix}:p2s:{w}"
        self._init_bus(_ServerShimFactory(server))
        self._initial_setup_done = False
        self._initial_setup_attempted = False
        self._startup_attempted = False
        self._startup_complete = False
        self._sessions = SessionReconciler(evennia.SERVER_SESSION_HANDLER)

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
        """Run process setup once, then negotiate session readiness."""
        if not self._initial_setup_done:
            if self._initial_setup_attempted:
                raise RuntimeError("redis bus: initial setup failed; process restart required")
            self._initial_setup_attempted = True
            self.factory.server.run_initial_setup()
            self._initial_setup_done = True
        return super().start_bus()

    def _apply_snapshot(self, payload):
        """Restore once at process start; reconcile surviving sessions later."""
        sessions = payload["sessions"]
        portal_id = self._handshake.pair[0][0]
        handler = self._sessions.handler
        if not self._startup_attempted:
            self._startup_attempted = True
            mode = payload.get("restart_mode") or "shutdown"
            self.factory.server.run_init_hooks(mode)
            restored = {
                sid: {key: value for key, value in data.items() if not key.startswith("_")}
                for sid, data in sessions.items()
                if data.get("_server_confirmed")
            }
            handler.portal_sessions_sync(restored, restart_mode=mode)
            self._sessions.adopt({sid: sessions[sid] for sid in restored}, portal_id)
            self._sessions.reconcile(sessions, portal_id)
            handler.portal_start_time = payload.get("portal_start_time")
            self._startup_complete = True
        elif not self._startup_complete:
            raise RuntimeError("redis bus: startup failed; process restart required")
        else:
            self._sessions.reconcile(sessions, portal_id)

    def _state_snapshot(self):
        """Return current Server authentication after reciprocal confirmation."""
        return {
            **self._sessions.server_state(),
            "spid": os.getpid(),
            "info_dict": self.factory.server.get_info_dict(),
        }

    def _ready(self):
        """Refresh each current session only after final confirmation is sent."""
        for session in list(self._sessions.handler.values()):
            try:
                session.at_transport_reconnect()
            except Exception:
                logger.log_trace("redis bus: session recovery hook failed")


class RedisPortalBus(_RedisBusMixin):
    _role = "portal"

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

    def _snapshot(self):
        """Capture current socket membership and its local revision."""
        handler = evennia.PORTAL_SESSION_HANDLER
        return handler.bus_revision, {
            "sessions": handler.get_bus_sync_data(),
            "restart_mode": self.factory.portal.server_restart_mode,
            "portal_start_time": self.factory.portal.start_time,
        }

    def _apply_state(self, payload):
        """Apply Server authority only to matching socket incarnations."""
        evennia.PORTAL_SESSION_HANDLER.apply_bus_state(payload)
        self.factory.portal.server_process_id = payload["spid"]
        self.factory.portal.server_info_dict = payload["info_dict"]

    def _ready(self):
        """Fire launcher readiness callbacks only after session application."""
        self.factory.server_connection = self
        evennia.PORTAL_SESSION_HANDLER.at_server_connection()
        server_process = self._handshake.pair[1][0]
        if getattr(self, "_ready_server_process", None) != server_process:
            self.factory.portal.server_restart_mode = None
            self._ready_server_process = server_process
        callbacks = self.factory.server_connect_callbacks
        self.factory.server_connect_callbacks = []
        for callback, args, kwargs in callbacks:
            try:
                callback(*args, **kwargs)
            except Exception:
                logger.log_trace("redis bus: connection-ready callback failed")
        self.send_Status2Launcher()

    def get_status(self):
        """Return the launcher status tuple.

        There is no socket to the server on this transport, so liveness is the
        server process rather than a connection. `_PidTransport` already
        answers exactly that question and is reused here.
        """

        from evennia.server.portal.amp_server import build_status

        portal = self.factory.portal
        return build_status(portal, _PidTransport(portal).connected)

    def send_Status2Launcher(self):
        """Push status to the launcher, if one is listening.

        Without this the Redis bus raised AttributeError on every PSYNC --
        `ipc_handlers_portal` calls this on the link it is given, and only the
        AMP protocol had it. The launcher therefore never learned the server
        had come back, waited for a status that could not arrive, and timed out
        its graceful shutdown on every restart.
        """

        conn = self.factory.launcher_connection
        if conn is None:
            return
        status = self.get_status()
        if hasattr(conn, "push_status"):
            conn.push_status(status)

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
