"""Portal/Server bus with confirmed recovery and bounded no-replay delivery."""

import os
import time

import psutil
from django.conf import settings

import evennia
from evennia.server import ipc_handlers_server
from evennia.server.bus_handshake import BusHandshake
from evennia.server.bus_result import PublicationResult, TransportUnavailable
from evennia.server.bus_sessions import SessionReconciler
from evennia.server.portal import amp, ipc_handlers_portal
from evennia.server.redis_transport import RedisTransport as _RedisTransport
from evennia.utils import clock, logger


def _stream_prefix():
    return getattr(settings, "REDIS_BUS_PREFIX", "evennia:bus")


def _worker_id():
    return str(getattr(settings, "SERVER_WORKER_ID", "0"))


class _RedisBusMixin:
    _send_stream = ""
    _read_stream = ""

    def _init_bus(self, shim_factory):
        self.factory = shim_factory
        self._transport = _RedisTransport(
            self._read_stream,
            self._receive_frame,
            on_failure=self._transport_failed,
            on_recovered=self._transport_recovered,
        )
        self._published_ready = False
        self._draining = False
        self._snapshot_waiters = {}
        self._shutdown_deadline = None
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
        return (
            self._handshake.state == "ready"
            and self._transport.online
            and self._published_ready
            and not self._draining
        )

    def _send_handshake(self, frame):
        """Publish fresh state using the typed admin serializer."""
        return self._transport.publish(
            self._send_stream,
            b"BusHandshake",
            amp.dumps_admin((0, {"sessiondata": frame})),
            handshake=True,
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
        elif cmdkey == b"BusSnapshotAck":
            payload = amp.loads_admin(data)[1]
            result = self._snapshot_waiters.pop(payload.get("snapshot_id"), None)
            if result is not None:
                result.succeed()
        elif cmdkey == b"BusStopping":
            self._draining = True
        elif cmdkey == b"MsgPortal2Server":
            if self._handshake.state == "ready" and not self._draining:
                self._on_frame(cmdkey, data)
        elif self._handshake.state in ("ready", "stopping"):
            self._on_frame(cmdkey, data)

    def _tick(self):
        """Own one cancellable heartbeat timer per running transport."""
        if self._transport._stop.is_set():
            return
        if self._transport.online:
            self._handshake.tick()
        self._tick_handle = clock.call_later(0.5, self._tick)

    def _unavailable(self):
        """Close readiness and invalidate queued work without rebuilding sessions."""
        self._published_ready = False
        self._transport.set_pair(None, ready=False)
        self._fail_snapshot_waiters("connection generation replaced")
        if self._transport.online:
            self._transport.fail("peer synchronization lost")

    def _fail_snapshot_waiters(self, reason):
        """Settle application waiters independently from intentional stopping."""
        waiters, self._snapshot_waiters = self._snapshot_waiters, {}
        for result in waiters.values():
            result.fail(TransportUnavailable(reason))

    def _transport_failed(self, reason):
        """Apply immediate transport failure to peer readiness and waiters."""
        self._published_ready = False
        self._fail_snapshot_waiters(reason)
        self._handshake.disconnect()
        logger.log_warn(f"redis bus unavailable: {reason}; interrupted work may have run")
        if self._role == "portal":
            for session in list(evennia.PORTAL_SESSION_HANDLER.values()):
                evennia.PORTAL_SESSION_HANDLER.bus_unavailable_notice(session)

    def _transport_recovered(self):
        """Start only fresh discovery after Redis and writer capacity recover."""
        if self._handshake.state != "stopping":
            self._handshake.tick()

    def _publish_control(self, key, payload):
        """Publish current-generation transport control through ordinary FIFO."""
        return self._transport.publish(
            self._send_stream,
            key,
            amp.dumps_admin((0, payload)),
            pair=self._handshake.pair,
        )

    def acknowledge_snapshot(self, snapshot_id):
        """Confirm Portal application of a current-generation final snapshot."""
        return self._publish_control(b"BusSnapshotAck", {"snapshot_id": snapshot_id})

    def begin_shutdown(self):
        """Close input while allowing teardown before final synchronization."""
        if self._handshake.state != "stopping":
            self._draining = True
            self._handshake.state = "stopping"
            self._publish_control(b"BusStopping", {})

    async def sync_sessions(self, sessiondata):
        """Wait for final Portal application within the shared shutdown deadline."""
        import asyncio
        from uuid import uuid4

        if self._snapshot_waiters:
            raise TransportUnavailable("final session synchronization already pending")
        if self._shutdown_deadline is None:
            self._shutdown_deadline = time.monotonic() + 5.0
        deadline = self._shutdown_deadline
        snapshot_id = uuid4().hex
        sessiondata = {
            sid: {
                **data,
                "_socket_id": self._sessions.records[sid]["_socket_id"],
                "_portal_flags": self._sessions.records[sid].get("protocol_flags", {}),
            }
            for sid, data in sessiondata.items()
            if sid in self._sessions.records
        }
        result = PublicationResult()
        self._snapshot_waiters[snapshot_id] = result
        try:
            publication = self.send_AdminServer2Portal(
                amp.DUMMYSESSION,
                operation=amp.SSYNC,
                sessiondata=sessiondata,
                snapshot_id=snapshot_id,
                clean=False,
            )
            publication.addErrback(result.fail)
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                await result
        finally:
            self._snapshot_waiters.pop(snapshot_id, None)
            if not result._future.done():
                result.fail(TransportUnavailable("final snapshot wait ended without confirmation"))

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
        """Return admission and publication status without automatic retransmission."""
        cmdkey = command.key
        if isinstance(cmdkey, str):
            cmdkey = cmdkey.encode()
        packed = kwargs.get("packed_data")
        allowed = self.ready
        if (
            self._role == "server"
            and not allowed
            and self._handshake.state in ("synchronizing", "stopping", "ready")
        ):
            allowed = cmdkey == b"MsgServer2Portal"
            if cmdkey == b"AdminServer2Portal":
                operation = amp.loads_admin(packed)[1].get("operation")
                allowed = operation in (
                    amp.SLOGIN,
                    amp.SSYNC,
                    amp.SDISCONN,
                    amp.SDISCONNALL,
                    amp.SCONN,
                )
        if not allowed:
            return PublicationResult.rejected(TransportUnavailable("peer is not ready"))
        return self._transport.publish(self._send_stream, cmdkey, packed, pair=self._handshake.pair)

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
            self._transport.settle_failure()
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
        self._published_ready = False
        self._transport.set_pair(self._handshake.pair, ready=False)
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
        self._published_ready = True
        self._draining = False
        self._transport.set_pair(self._handshake.pair, ready=True)
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
        self._published_ready = False
        self._transport.set_pair(self._handshake.pair, ready=False)
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
        self._published_ready = True
        self._draining = False
        self.factory.portal._lifecycle_unconfirmed = False
        self._transport.set_pair(self._handshake.pair, ready=True)
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
