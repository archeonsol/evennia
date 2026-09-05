"""Opt-in real Redis tests with separate actual Portal and Server bus processes.

Set EVENNIA_REDIS_SERVER to a redis-server executable. Session handlers expose
observable lifecycle boundaries; this does not bootstrap full game services.
"""

import asyncio
import multiprocessing
import os
import time
import traceback
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from uuid import uuid4


def _peer_process(role, connection, url, prefix, log_path):
    """Run one actual bus on its own event loop with observable session state."""
    with open(log_path, "a", buffering=1) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            import django

            os.environ["DJANGO_SETTINGS_MODULE"] = "evennia.settings_default"
            django.setup()
            asyncio.run(_run_peer(role, connection, url, prefix))
        except BaseException:
            connection.send({"event": "error", "traceback": traceback.format_exc()})
            raise
        finally:
            connection.close()


async def _run_peer(role, connection, url, prefix):
    """Use production scheduling, handshake, serializers and Redis workers."""
    from django.conf import settings

    import evennia
    from evennia.server.portal.portalsessionhandler import PortalSessionHandler
    from evennia.server.redis_bus import RedisPortalBus, RedisServerBus
    from evennia.server.session import Session
    from evennia.utils import clock

    settings.REDIS_BUS_URL = url
    settings.REDIS_BUS_PREFIX = prefix
    settings.SERVER_WORKER_ID = "0"
    clock.bind_loop(asyncio.get_running_loop())
    counters = {"setup": 0, "init": 0, "restore": 0, "connect": 0, "recovery": 0}

    def emit(event, **payload):
        """Expose process observations independently from Redis traffic."""
        connection.send({"event": event, **payload})

    class RuntimeSession(Session):
        """Retain a menu identity and report recovery without game hooks."""

        def at_transport_reconnect(self):
            """Count actual bus recovery callback invocations."""
            counters["recovery"] += 1

    class ServerSessions(dict):
        """Expose execution and startup boundaries without database objects."""

        def create(self, data):
            """Construct a session with the actual Session sync contract."""
            session = RuntimeSession()
            session.init_session("test", "local", self)
            session.load_sync_data(data)
            session.menu = object()
            self[session.sessid] = session

        def portal_connect(self, data):
            """Record normal new-socket admission."""
            counters["connect"] += 1
            self.create(data)

        def portal_sessions_sync(self, data, restart_mode):
            """Record process reconstruction separately from admission."""
            counters["restore"] += 1
            for values in data.values():
                self.create(values)

        def portal_disconnect(self, session):
            """Remove the observed socket through normal reconciliation."""
            self.pop(session.sessid)

        def data_in(self, session, **kwargs):
            """Observe execution before deliberately blocking a killed process."""
            text = kwargs["text"][0][0]
            emit("executed", text=text)
            if text == "block-after-observation":
                time.sleep(60)

        def get_all_sync_data(self):
            """Serialize current runtime sessions through the actual base class."""
            return {sid: session.get_sync_data() for sid, session in self.items()}

    class PortalSessions(PortalSessionHandler):
        """Use real membership and application with observable local output."""

        def data_out(self, session, **kwargs):
            """Observe output without opening player sockets."""
            emit("output")

    def initial_setup():
        """Count process setup."""
        counters["setup"] += 1

    def init_hooks(mode):
        """Count startup hooks without invoking database setup."""
        counters["init"] += 1

    if role == "server":
        handler = ServerSessions()
        evennia.SERVER_SESSION_HANDLER = handler
        service = SimpleNamespace(
            run_initial_setup=initial_setup, run_init_hooks=init_hooks, get_info_dict=lambda: {}
        )
        evennia.EVENNIA_SERVER_SERVICE = service
        bus = service.portal_bus = RedisServerBus(service)
    else:
        handler = PortalSessions()
        socket = Session()
        socket.init_session("test", "local", handler)
        socket.sessid = 1
        socket.server_connected = False
        handler._ensure_bus_socket(socket)
        handler[1] = socket
        evennia.PORTAL_SESSION_HANDLER = handler
        service = SimpleNamespace(server_restart_mode="shutdown", start_time=time.time())
        evennia.EVENNIA_PORTAL_SERVICE = service
        bus = service.server_amp = RedisPortalBus(service)
    bus.start_bus()
    emit("started", pid=os.getpid())
    held_acks = []
    original_ack = bus.acknowledge_snapshot
    pending_sync = None
    checkpoint_session = checkpoint_menu = None
    previous_ready = None

    async def synchronize(token):
        """Report completion only after actual Portal application acknowledgment."""
        try:
            await bus.sync_sessions(handler.get_all_sync_data())
            emit("synced", token=token)
        except Exception as error:
            emit("sync_failed", token=token, error=repr(error))

    def delay_ack(snapshot_id):
        """Hold only the application acknowledgment, after actual application."""
        held_acks.append(snapshot_id)
        emit("ack_held", uid=handler[1].uid)

    try:
        running = True
        while running:
            ready = bus.ready
            if ready != previous_ready:
                emit("readiness", ready=ready)
                previous_ready = ready
            while connection.poll():
                command = connection.recv()
                op, token = command["op"], command.get("token")
                if op == "stop":
                    running = False
                elif op == "checkpoint":
                    checkpoint_session = handler[1]
                    checkpoint_menu = checkpoint_session.menu
                    if command.get("authenticate"):
                        checkpoint_session.uid = 73
                        checkpoint_session.logged_in = True
                    emit("checkpointed", token=token)
                elif op == "status":
                    current = handler.get(1)
                    emit(
                        "status",
                        token=token,
                        ready=bus.ready,
                        pair=bus._handshake.pair,
                        session_retained=current is checkpoint_session,
                        menu_retained=getattr(current, "menu", None) is checkpoint_menu,
                        uid=getattr(current, "uid", None),
                        counters=counters.copy(),
                        syncing=pending_sync is not None and not pending_sync.done(),
                    )
                elif op == "send":
                    result = bus.send_MsgPortal2Server(handler[1], text=[[command["text"]], {}])
                    emit("admitted", token=token, admitted=bool(result))
                elif op == "stale":
                    from evennia.server.portal import amp

                    result = bus._transport.publish(
                        bus._send_stream,
                        b"MsgPortal2Server",
                        amp.dumps_session((1, {"text": [[command["text"]], {}]})),
                        pair=command["pair"],
                    )
                    emit("admitted", token=token, admitted=bool(result))
                elif op == "delay_ack":
                    bus.acknowledge_snapshot = delay_ack
                    emit("ack_delay_enabled", token=token)
                elif op == "release_ack":
                    for snapshot_id in held_acks:
                        original_ack(snapshot_id)
                    held_acks.clear()
                elif op == "sync":
                    handler[1].uid = 73
                    bus.begin_shutdown()
                    pending_sync = asyncio.create_task(synchronize(token))
            await asyncio.sleep(0.01)
    finally:
        if pending_sync is not None and not pending_sync.done():
            pending_sync.cancel()
            await asyncio.gather(pending_sync, return_exceptions=True)
        bus.stop_bus()


@skipUnless(os.environ.get("EVENNIA_REDIS_SERVER"), "set EVENNIA_REDIS_SERVER for real Redis tests")
class TestRealBusProcesses(TestCase):
    """The parent owns cleanup even when a Server event loop is blocked."""

    def setUp(self):
        """Start private Redis and retain every process before waiting for it."""
        from evennia.server.tests.redis_live_helpers import RedisProcess

        self.redis = RedisProcess()
        self.addCleanup(self.redis.close)
        self.context = multiprocessing.get_context("spawn")
        self.peers = []
        self.addCleanup(self._close_peers)
        self.prefix = "bus-process:" + uuid4().hex

    def _spawn(self, role):
        """Register a spawned peer before it can fail during startup."""
        import tempfile

        parent, child = self.context.Pipe()
        log = tempfile.NamedTemporaryFile(prefix="bus-peer-", suffix=".log", delete=False)
        log.close()
        process = self.context.Process(
            target=_peer_process, args=(role, child, self.redis.url, self.prefix, log.name)
        )
        peer = {"process": process, "pipe": parent, "events": [], "log": log.name}
        self.peers.append(peer)
        try:
            process.start()
        finally:
            child.close()
        self._wait(peer, "started", timeout=20)
        return peer

    def _close_peers(self):
        """Terminate and reap peers without depending on their bus or event loop."""
        for peer in self.peers:
            process = peer["process"]
            if process.pid is None:
                peer["pipe"].close()
                continue
            if process.is_alive():
                process.terminate()
            process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
            peer["pipe"].close()

    def _wait(self, peer, event, token=None, timeout=12):
        """Wait for independent IPC evidence and include child traces on failure."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, item in enumerate(peer["events"]):
                if item["event"] == event and (token is None or item.get("token") == token):
                    return peer["events"].pop(index)
                if item["event"] == "error":
                    self.fail(item["traceback"])
            if peer["pipe"].poll(0.02):
                try:
                    peer["events"].append(peer["pipe"].recv())
                except EOFError:
                    break
        with open(peer["log"]) as log:
            self.fail(f"Timed out waiting for {event}: {peer['events']!r}\n{log.read()}")

    def _command(self, peer, op, **payload):
        """Correlate parent requests without using the transport under test."""
        token = uuid4().hex
        peer["pipe"].send({"op": op, "token": token, **payload})
        return token

    def _status(self, peer):
        """Read process-local state through the independent pipe."""
        return self._wait(peer, "status", self._command(peer, "status"))

    def _checkpoint(self, server, *, authenticate=False):
        """Retain original references so allocator reuse cannot fake identity."""
        token = self._command(server, "checkpoint", authenticate=authenticate)
        self._wait(server, "checkpointed", token)
        return self._status(server)

    def _ready(self, portal, server, previous_pair=None):
        """Require matching newly confirmed generations in both processes."""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            pstate, sstate = self._status(portal), self._status(server)
            if (
                pstate["ready"]
                and sstate["ready"]
                and pstate["pair"] == sstate["pair"]
                and pstate["pair"] != previous_pair
            ):
                return pstate, sstate
            time.sleep(0.03)
        self.fail(f"Peers did not converge: {pstate!r}, {sstate!r}")

    def _pair(self, first="portal"):
        """Start the actual buses in a selected order."""
        peers = {first: self._spawn(first)}
        other = "server" if first == "portal" else "portal"
        peers[other] = self._spawn(other)
        self._ready(peers["portal"], peers["server"])
        return peers["portal"], peers["server"]

    def test_both_startup_orders(self):
        """Neither process requires the other's initial publication."""
        for first in ("portal", "server"):
            with self.subTest(first=first):
                portal, server = self._pair(first)
                _, state = self._ready(portal, server)
                self.assertEqual(state["counters"]["init"], 1)
                self._close_peers()
                self.peers.clear()
                self.prefix = "bus-process:" + uuid4().hex

    def test_killed_server_does_not_repeat_observed_action(self):
        """Kill after externally observed execution and retain the real stream."""
        portal, server = self._pair()
        self._command(portal, "send", text="block-after-observation")
        self.assertEqual(self._wait(server, "executed")["text"], "block-after-observation")
        server["process"].kill()
        server["process"].join(3)
        replacement = self._spawn("server")
        self._ready(portal, replacement)
        self._command(portal, "send", text="fresh")
        self.assertEqual(self._wait(replacement, "executed")["text"], "fresh")
        rows = self.redis.client.xrange(self.prefix + ":p2s:0")
        self.assertEqual(sum(row[1].get(b"c") == b"MsgPortal2Server" for row in rows), 2)
        self.assertNotEqual(server["process"].pid, replacement["process"].pid)

    def test_client_disconnect_recovers_identity_and_rejects_stale_frame(self):
        """Kill Redis connections while Redis and both peer processes survive."""
        portal, server = self._pair()
        before = self._checkpoint(server, authenticate=True)
        redis_pid = self.redis.process.pid
        self.redis.client.execute_command("CLIENT", "KILL", "TYPE", "normal", "SKIPME", "yes")
        _, after = self._ready(portal, server, previous_pair=before["pair"])
        self.assertEqual(self.redis.process.pid, redis_pid)
        self.assertIsNone(self.redis.process.poll())
        self.assertTrue(portal["process"].is_alive())
        self.assertTrue(server["process"].is_alive())
        self.assertTrue(after["session_retained"])
        self.assertTrue(after["menu_retained"])
        self.assertEqual(after["uid"], 73)
        self.assertEqual(self._status(portal)["uid"], 73)
        self.assertEqual(after["counters"]["init"], 1)
        self._command(portal, "stale", text="stale", pair=before["pair"])
        self._command(portal, "send", text="fresh-after-recovery")
        self.assertEqual(self._wait(server, "executed")["text"], "fresh-after-recovery")
        rows = self.redis.client.xrange(self.prefix + ":p2s:0")
        self.assertEqual(sum(row[1].get(b"c") == b"MsgPortal2Server" for row in rows), 2)

    def test_final_snapshot_waits_for_delayed_application_ack(self):
        """Redis publication alone cannot complete graceful session synchronization."""
        portal, server = self._pair()
        token = self._command(portal, "delay_ack")
        self._wait(portal, "ack_delay_enabled", token)
        token = self._command(server, "sync")
        held = self._wait(portal, "ack_held")
        self.assertEqual(held["uid"], 73)
        self.assertTrue(self._status(server)["syncing"])
        self._command(portal, "release_ack")
        self._wait(server, "synced", token)

    def test_trimmed_history_forces_reconciliation_before_new_actions(self):
        """A real empty retained stream cannot silently preserve readiness."""
        portal, server = self._pair()
        before = self._checkpoint(server)
        self.redis.client.xtrim(self.prefix + ":p2s:0", maxlen=0, approximate=False)
        _, after = self._ready(portal, server, previous_pair=before["pair"])
        self.assertTrue(after["session_retained"])
        self.assertTrue(after["menu_retained"])
        self.assertEqual(after["counters"]["init"], 1)
        self._command(portal, "send", text="fresh-after-trim")
        self.assertEqual(self._wait(server, "executed")["text"], "fresh-after-trim")

    def test_redis_restart_loses_data_but_preserves_surviving_sessions(self):
        """Restart the real Redis process and prove both lost data and recovery."""
        portal, server = self._pair()
        before = self._checkpoint(server, authenticate=True)
        redis_pid = self.redis.process.pid
        peer_pids = (portal["process"].pid, server["process"].pid)
        sentinel = self.prefix + ":restart-sentinel"
        self.redis.client.set(sentinel, "must disappear")
        self.redis.stop()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if not self._status(portal)["ready"] and not self._status(server)["ready"]:
                break
            time.sleep(0.02)
        else:
            self.fail("Peers did not close readiness while Redis was stopped")
        self.redis.restart()
        self.assertNotEqual(self.redis.process.pid, redis_pid)
        self.assertIsNone(self.redis.client.get(sentinel))
        _, after = self._ready(portal, server, previous_pair=before["pair"])
        self.assertEqual((portal["process"].pid, server["process"].pid), peer_pids)
        self.assertTrue(after["session_retained"])
        self.assertTrue(after["menu_retained"])
        self.assertEqual(after["uid"], 73)
        self.assertEqual(self._status(portal)["uid"], 73)
        self.assertEqual(after["counters"]["init"], 1)
        self._command(portal, "send", text="fresh-after-redis-restart")
        self.assertEqual(self._wait(server, "executed")["text"], "fresh-after-redis-restart")
