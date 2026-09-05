"""
Integration tests for the redis Streams Portal<->Server bus (plain XADD/XREAD).

Uses fakeredis so CI does not need a live redis daemon.
"""

import asyncio
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import fakeredis
from django.test import SimpleTestCase, TestCase, override_settings

import evennia
from evennia.server import ipc_schema, redis_bus, session
from evennia.server.portal import amp, amp_server
from evennia.server.portal.portalsessionhandler import PortalSessionHandler
from evennia.server.portal.service import EvenniaPortalService
from evennia.server.redis_bus import (
    RedisPortalBus,
    RedisServerBus,
    _PidTransport,
    _RedisTransport,
)
from evennia.server.service import EvenniaServerService
from evennia.server.sessionhandler import ServerSessionHandler
from evennia.utils import clock

_BUS_SETTINGS = {
    "SERVER_PORTAL_BUS": "redis",
    "REDIS_BUS_URL": "redis://127.0.0.1:6379/15",
    "REDIS_BUS_PREFIX": "evennia:testbus",
    "SERVER_WORKER_ID": "0",
}


def _sync_call_from_thread(fn, *args, **kwargs):
    """Run frame dispatch immediately (tests have no reactor thread)."""
    return fn(*args, **kwargs)


def _drain_bus(seconds=0.15):
    """Let redis reader threads pick up published frames."""
    time.sleep(seconds)


class _BusTestResources:
    """Own one synchronous bus fixture's globals, scheduling, and Redis patch."""

    def __init__(self):
        """Acquire patches with cleanup registered before service construction."""
        self.stack = ExitStack()
        self.buses = []
        self.handles = []
        self.loop = asyncio.new_event_loop()
        names = (
            "EVENNIA_SERVER_SERVICE",
            "SERVER_SESSION_HANDLER",
            "EVENNIA_PORTAL_SERVICE",
            "PORTAL_SESSION_HANDLER",
        )
        saved = {name: getattr(evennia, name) for name in names}
        self.stack.enter_context(patch.multiple(evennia, **saved))
        self.fake = fakeredis.FakeRedis(decode_responses=False)
        self.stack.callback(self.fake.close)
        self.stack.enter_context(patch("redis.Redis.from_url", return_value=self.fake))
        self.stack.enter_context(patch.object(clock, "_get_loop", return_value=self.loop))
        self.stack.enter_context(patch.object(clock, "_pending_when_running", []))
        self.stack.enter_context(patch.object(clock, "call_from_thread", _sync_call_from_thread))
        call_later = clock.call_later

        def schedule(*args, **kwargs):
            """Track real cancellable timer handles owned by this fixture."""
            handle = call_later(*args, **kwargs)
            self.handles.append(handle)
            return handle

        self.stack.enter_context(patch.object(clock, "call_later", side_effect=schedule))
        self.stack.callback(self._settle)

    def close(self):
        """Release scheduling before patches and service globals are restored."""
        self.stack.close()

    def _settle(self):
        """Stop all readers before driving cancellation on the private loop."""
        errors = []
        survivors = False
        for bus in self.buses:
            try:
                bus._transport.stop()
            except Exception as error:
                errors.append(error)
            for name in ("_writer", "_reader", "_cleanup"):
                worker = getattr(bus._transport, name)
                if worker is not None and worker.is_alive():
                    survivors = True
                    errors.append(AssertionError(f"bus fixture left {name} running"))
        for handle in self.handles:
            handle.cancel()
        try:
            tasks = list(asyncio.all_tasks(self.loop))
            for task in tasks:
                task.cancel()
            if tasks and not survivors:
                results = self.loop.run_until_complete(
                    asyncio.gather(*tasks, return_exceptions=True)
                )
                errors.extend(result for result in results if isinstance(result, Exception))
        finally:
            self.loop.close()
        if errors:
            raise ExceptionGroup("bus fixture cleanup failed", errors)


@override_settings(**_BUS_SETTINGS)
class TestRedisBus(TestCase):
    def setUp(self):
        self.resources = _BusTestResources()
        self.addCleanup(self.resources.close)
        self.fake_redis = self.resources.fake

        self.server = EvenniaServerService()
        self.server.run_initial_setup = MagicMock()
        self.server.run_init_hooks = MagicMock()
        evennia.EVENNIA_SERVER_SERVICE = self.server
        evennia.SERVER_SESSION_HANDLER = ServerSessionHandler()
        evennia.SERVER_SESSION_HANDLER.data_in = MagicMock()
        evennia.SERVER_SESSION_HANDLER.data_out = MagicMock()
        evennia.SERVER_SESSION_HANDLER.portal_disconnect_all = MagicMock()
        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync = MagicMock()

        self.session = MagicMock()
        self.session.sessid = 1
        evennia.SERVER_SESSION_HANDLER[1] = self.session

        self.portal = EvenniaPortalService()
        evennia.EVENNIA_PORTAL_SERVICE = self.portal
        self.portalsession = session.Session()
        self.portalsession.sessid = 1
        evennia.PORTAL_SESSION_HANDLER = PortalSessionHandler()
        evennia.PORTAL_SESSION_HANDLER[1] = self.portalsession
        evennia.PORTAL_SESSION_HANDLER.data_in = MagicMock()
        evennia.PORTAL_SESSION_HANDLER.data_out = MagicMock()
        evennia.PORTAL_SESSION_HANDLER.get_all_sync_data = MagicMock(return_value=[])
        evennia.PORTAL_SESSION_HANDLER.at_server_connection = MagicMock()

        self.amp_factory = amp_server.AMPServerFactory(self.portal)
        self.server_bus = RedisServerBus(self.server)
        self.resources.buses.append(self.server_bus)
        self.server.portal_bus = self.server_bus
        self.portal_bus = RedisPortalBus(self.portal, factory=self.amp_factory)
        self.resources.buses.append(self.portal_bus)
        self.portal.server_bus = self.portal_bus
        self.amp_factory.server_connection = self.portal_bus

        # Match production lifecycle: the Portal is subscribed to s2p before the
        # Server boots and announces itself via PSYNC. Starting the server bus
        # first would publish PSYNC before the portal reader exists and the
        # ``$`` cursor would skip it. Let the portal reader reach its first
        # blocking xread before the server publishes.
        self.portal_bus.start_bus()
        time.sleep(0.05)
        self.server_bus.start_bus()

    def test_stream_topology(self):
        prefix = _BUS_SETTINGS["REDIS_BUS_PREFIX"]
        self.assertEqual(self.server_bus._send_stream, f"{prefix}:s2p")
        self.assertEqual(self.server_bus._read_stream, f"{prefix}:p2s:0")
        self.assertEqual(self.portal_bus._send_stream, f"{prefix}:p2s:0")
        self.assertEqual(self.portal_bus._read_stream, f"{prefix}:s2p")

    def test_msgportal2server_roundtrip(self):
        self.portal_bus.send_MsgPortal2Server(self.portalsession, text={"foo": "bar"})
        _drain_bus()
        evennia.SERVER_SESSION_HANDLER.data_in.assert_called_with(self.session, text={"foo": "bar"})

    def test_msgserver2portal_roundtrip(self):
        self.server_bus.send_MsgServer2Portal(self.session, text={"hello": "world"})
        _drain_bus()
        evennia.PORTAL_SESSION_HANDLER.data_out.assert_called_with(
            self.portalsession, text={"hello": "world"}
        )

    def test_psync_on_server_start(self):
        _drain_bus()
        self.assertIsNotNone(self.portal.server_process_id)
        evennia.PORTAL_SESSION_HANDLER.at_server_connection.assert_called()
        self.server.run_init_hooks.assert_called_once_with("shutdown")

    def test_admin_pdisconnall(self):
        self.portal_bus.send_AdminPortal2Server(amp.DUMMYSESSION, operation=amp.PDISCONNALL)
        _drain_bus()
        evennia.SERVER_SESSION_HANDLER.portal_disconnect_all.assert_called()

    def test_admin_ssync_on_portal(self):
        evennia.PORTAL_SESSION_HANDLER.server_session_sync = MagicMock()
        self.server_bus.send_AdminServer2Portal(
            amp.DUMMYSESSION,
            operation=amp.SSYNC,
            sessiondata=[{"sessid": 1}],
            clean=True,
        )
        _drain_bus()
        evennia.PORTAL_SESSION_HANDLER.server_session_sync.assert_called_once_with(
            [{"sessid": 1}], True
        )

    def test_ipc_schema_session_wire(self):
        env = ipc_schema.SessionEnvelope(sessid=1, kwargs={"text": [["hi"], {}]})
        wire = env.to_wire()
        self.server_bus._on_frame(b"MsgPortal2Server", wire)
        evennia.SERVER_SESSION_HANDLER.data_in.assert_called_with(self.session, text=[["hi"], {}])

    def test_pid_transport_disconnect(self):
        transport = _PidTransport(self.portal)
        self.portal.server_process_id = 999999999
        self.assertFalse(transport.connected)

    def test_pid_transport_connected(self):
        import os

        transport = _PidTransport(self.portal)
        self.portal.server_process_id = os.getpid()
        self.assertTrue(transport.connected)


@override_settings(**_BUS_SETTINGS)
class TestRedisBusSerdeLimits(SimpleTestCase):
    """enforce_limits asymmetry: trusted Server output vs untrusted Portal input."""

    def test_trusted_large_server_output_passes(self):
        big = "x" * 50000
        env = ipc_schema.SessionEnvelope(sessid=1, kwargs={"text": [[big], {}]})
        wire = env.to_wire()
        sessid, kwargs = ipc_schema.parse_session(wire, enforce_limits=False)
        self.assertEqual(sessid, 1)
        self.assertIn("text", kwargs)

    def test_untrusted_oversized_portal_input_rejected(self):
        big = "x" * 70000
        env = ipc_schema.SessionEnvelope(sessid=1, kwargs={"text": [[big], {}]})
        wire = env.to_wire()
        with self.assertRaises(ValueError):
            ipc_schema.parse_session(wire, enforce_limits=True)

    def test_parse_admin_returns_wire_chr(self):
        env = ipc_schema.AdminEnvelope(
            sessid=0, operation=ipc_schema.AdminOperation.PSYNC, kwargs={"spid": 1}
        )
        wire = env.to_wire()
        sessid, operation, kwargs = ipc_schema.parse_admin(wire)
        self.assertEqual(operation, amp.PSYNC)
        self.assertEqual(sessid, 0)
        self.assertEqual(kwargs.get("spid"), 1)


@override_settings(**_BUS_SETTINGS)
class TestRedisTransportBootFailFast(SimpleTestCase):
    """Boot ping failure fails fast: log fatal, stop the loop, no reader thread."""

    def test_boot_ping_failure_stops_loop(self):
        import redis as redis_mod

        fake = MagicMock()
        fake.ping.side_effect = redis_mod.exceptions.ConnectionError("refused")
        with (
            patch("redis.Redis.from_url", return_value=fake),
            patch.object(redis_bus.logger, "log_err") as mock_log_err,
            patch.object(redis_bus.clock, "stop_loop") as mock_stop,
        ):
            transport = _RedisTransport("evennia:testbus:s2p", lambda *a: None)
            transport.start()
        mock_log_err.assert_called_once()
        mock_stop.assert_called_once()
        # writer/reader threads must not start on a dead connection
        self.assertIsNone(transport._reader)
        self.assertIsNone(transport._writer)


class TestReceiveServer2PortalSurfacesErrors(SimpleTestCase):
    """The trusted Server->Portal out path must not swallow genuine errors:
    redis dispatches each frame independently, so a data_out bug should surface
    rather than hide in a trace line (a holdover from AMP per-frame isolation)."""

    def test_data_out_error_propagates(self):
        from evennia.server.portal import ipc_handlers_portal

        handler = MagicMock()
        handler.get.return_value = object()
        handler.data_out.side_effect = RuntimeError("data_out bug")
        with (
            patch.object(ipc_handlers_portal, "evennia") as mock_ev,
            patch.object(
                ipc_handlers_portal.ipc_schema,
                "parse_session",
                return_value=(1, {"text": "hi"}),
            ),
        ):
            mock_ev.PORTAL_SESSION_HANDLER = handler
            with self.assertRaises(RuntimeError):
                ipc_handlers_portal.receive_server2portal(b"frame")


class TestUnsupportedBusFailsHard(SimpleTestCase):
    """redis is the only supported Portal<->Server bus: any other setting aborts
    boot rather than silently forcing redis."""

    # EvenniaServerService.__init__ runs sqlite3_prep(), which opens a cursor.
    databases = {"default"}

    @override_settings(SERVER_PORTAL_BUS="amp")
    def test_server_register_amp_raises_on_non_redis(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            EvenniaServerService().register_amp()

    @override_settings(SERVER_PORTAL_BUS="amp")
    def test_portal_register_amp_raises_on_non_redis(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            EvenniaPortalService().register_amp()


@override_settings(**_BUS_SETTINGS)
class TestRedisTransportStop(SimpleTestCase):
    """stop() drops only threads that actually exited; a wedged thread is kept
    (and logged) so a later start() sees it via is_alive() and never duplicates."""

    def test_stop_keeps_wedged_thread_handle_and_warns(self):
        transport = _RedisTransport("evennia:testbus:s2p", lambda *a: None)
        exited = MagicMock()
        exited.is_alive.return_value = False
        wedged = MagicMock()
        wedged.is_alive.return_value = True
        transport._writer = exited
        transport._reader = wedged
        with patch.object(redis_bus.logger, "log_warn") as mock_log_warn:
            transport.stop()
        # exited thread handle cleared; wedged one retained so start()'s
        # is_alive() guard prevents spawning a duplicate reader
        self.assertIsNone(transport._writer)
        self.assertIs(transport._reader, wedged)
        self.assertTrue(mock_log_warn.called)


@override_settings(**_BUS_SETTINGS)
class TestRedisTransportQueueBound(SimpleTestCase):
    """Publish queue is bounded: overflow drops the newest frame, logs once per stall."""

    @patch.object(redis_bus, "_MAX_QUEUE", 5)
    @patch.object(redis_bus.logger, "log_err")
    def test_publish_queue_bounded_drops_newest_logs_once(self, mock_log_err):
        # writer thread never started, so nothing drains the queue
        transport = _RedisTransport("evennia:testbus:s2p", lambda *a: None)
        for i in range(20):
            transport.publish("stream", b"MsgPortal2Server", str(i).encode())
        # capped at _MAX_QUEUE; the first 5 frames are kept, the newer 15 dropped
        self.assertEqual(transport._q.qsize(), 5)
        # one log for the whole stall episode, not one per dropped frame
        self.assertEqual(mock_log_err.call_count, 1)


@override_settings(**_BUS_SETTINGS)
@patch("evennia.utils.clock.call_from_thread", _sync_call_from_thread)
class TestRedisTransportDurability(TestCase):
    """Consumer-group durability: no ``$`` skip, un-acked reclaim, writer retry,
    control vs data drop policy."""

    def setUp(self):
        self.fake = fakeredis.FakeRedis(decode_responses=False)
        self.p = patch("redis.Redis.from_url", return_value=self.fake)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.stream = "evennia:testbus:s2p"

    def _transport(self, sink):
        t = _RedisTransport(self.stream, sink)
        t._client = self.fake
        return t

    def test_frames_present_before_reader_are_not_skipped(self):
        # Frames the peer wrote while this side was "down" must be delivered,
        # not skipped the way a ``$`` cursor did.
        got = []
        t = self._transport(lambda cmd, data: got.append(data))
        self.fake.xadd(self.stream, {redis_bus._CMD: b"MsgServer2Portal", redis_bus._DATA: b"a"})
        self.fake.xadd(self.stream, {redis_bus._CMD: b"MsgServer2Portal", redis_bus._DATA: b"b"})
        t._ensure_group()
        resp = self.fake.xreadgroup(t._group, t._consumer, {self.stream: ">"}, count=10)
        for _s, entries in resp:
            for eid, fields in entries:
                t._dispatch(eid, fields)
        self.assertEqual(got, [b"a", b"b"])

    def test_unacked_frames_reclaimed_on_restart(self):
        # Delivered-but-not-acked frames (a crash between dispatch and ack) are
        # redelivered on the next start via the pending reclaim.
        self.fake.xadd(self.stream, {redis_bus._CMD: b"MsgServer2Portal", redis_bus._DATA: b"x"})
        t1 = self._transport(lambda *a: None)
        t1._ensure_group()
        # deliver without acking -> stays pending for this consumer
        self.fake.xreadgroup(t1._group, t1._consumer, {self.stream: ">"}, count=10)

        got = []
        t2 = self._transport(lambda cmd, data: got.append(data))
        t2._drain_pending()
        self.assertEqual(got, [b"x"])

    def test_xadd_retries_then_succeeds(self):
        client = MagicMock()
        client.xadd.side_effect = [Exception("down"), Exception("down"), None]
        t = _RedisTransport(self.stream, lambda *a: None)
        t._client = client
        self.assertTrue(t._xadd_with_retry(self.stream, b"MsgX", b"d", attempts=3))
        self.assertEqual(client.xadd.call_count, 3)

    def test_xadd_gives_up_reports_false(self):
        client = MagicMock()
        client.xadd.side_effect = Exception("down")
        t = _RedisTransport(self.stream, lambda *a: None)
        t._client = client
        self.assertFalse(t._xadd_with_retry(self.stream, b"MsgX", b"d", attempts=2))

    def test_control_frame_classification(self):
        self.assertTrue(_RedisTransport._is_control(b"AdminPortal2Server"))
        self.assertTrue(_RedisTransport._is_control(b"AdminServer2Portal"))
        self.assertFalse(_RedisTransport._is_control(b"MsgPortal2Server"))
        self.assertFalse(_RedisTransport._is_control(b""))


class PidAliveTest(SimpleTestCase):
    """`_pid_alive` reports liveness for real pids without a running loop or DB."""

    def test_own_pid_is_alive(self):
        self.assertTrue(redis_bus._pid_alive(os.getpid()))

    def test_falsy_pid_is_not_alive(self):
        # guards the None/0/"" cases the callers pass when no process is known
        self.assertFalse(redis_bus._pid_alive(None))
        self.assertFalse(redis_bus._pid_alive(0))
        self.assertFalse(redis_bus._pid_alive(""))

    def test_reaped_pid_is_not_alive(self):
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait()  # child exits and is reaped, so its pid is truly gone
        self.assertFalse(redis_bus._pid_alive(proc.pid))


class TestBoundedTransportStop(SimpleTestCase):
    """Real workers exercise the bounded stop and cleanup ownership contract."""

    def setUp(self):
        """Isolate transport resources and release workers before restoring patches."""
        import threading

        self.threading = threading
        self.transport = _RedisTransport("test:stop", MagicMock())
        self.transport._client = MagicMock()
        self.releases = []
        self.workers = []
        budget = patch.object(redis_bus, "_STOP_TIMEOUT", 0.05, create=True)
        budget.start()
        self.addCleanup(budget.stop)
        logs = patch.object(redis_bus, "logger")
        self.logs = logs.start()
        self.addCleanup(logs.stop)
        self.addCleanup(self._release)

    def _release(self):
        """Release controlled calls and the original implementation's queue wait."""
        self.transport._stop.set()
        for event in self.releases:
            event.set()
        while not self.transport._q.empty():
            self.transport._q.get_nowait()
        self.transport._q.put_nowait(None)
        for worker in self.workers:
            worker.join(2)
        cleanup = getattr(self.transport, "_cleanup", None)
        if cleanup is not None:
            cleanup.join(2)

    def _block(self):
        """Return a controllable blocking operation and entry/release events."""
        entered, release = self.threading.Event(), self.threading.Event()
        self.releases.append(release)

        def block(*args, **kwargs):
            """Signal entry and wait for test release."""
            entered.set()
            release.wait(5)

        return block, entered, release

    def _worker(self, target):
        """Retain a real daemon for cleanup."""
        worker = self.threading.Thread(target=target, daemon=True)
        self.workers.append(worker)
        worker.start()
        return worker

    def _bounded_stop(self):
        """Use an outer watchdog to detect hangs in the original code."""
        worker = self._worker(self.transport.stop)
        worker.join(0.5)
        self.assertFalse(worker.is_alive(), "stop exceeded its shared budget")

    def test_full_queue_blocked_workers_and_cleanup_ownership(self):
        """Stop uses one deadline and closes only after both workers exit."""
        self.transport._q = redis_bus.queue.Queue(maxsize=1)
        self.transport.publish("s", b"Msg", b"old")
        for name in ("_writer", "_reader"):
            block, entered, release = self._block()
            setattr(self.transport, name, self._worker(block))
            self.assertTrue(entered.wait(1))
        client = self.transport._client
        self._bounded_stop()
        cleanup = self.transport._cleanup
        self._bounded_stop()
        self.assertIs(self.transport._cleanup, cleanup)
        client.close.assert_not_called()
        with self.assertRaises(RuntimeError):
            self.transport.start()
        with self.assertRaises(RuntimeError):
            self.transport.publish("s", b"Msg", b"new")
        self.releases[0].set()
        self.transport._writer.join(1)
        client.close.assert_not_called()
        self.releases[1].set()
        cleanup.join(1)
        self.assertFalse(cleanup.is_alive())
        self.transport.stop()
        client.close.assert_called_once()

    def test_writer_only_survivor_rejects_restart(self):
        """A surviving writer cannot be orphaned when its reader has exited."""
        import redis

        block, entered, release = self._block()
        self.transport._writer = self._worker(block)
        self.assertTrue(entered.wait(1))
        self.transport._stop.set()
        with patch.object(redis.Redis, "from_url") as factory:
            with self.assertRaises(RuntimeError):
                self.transport.start()
        factory.assert_not_called()

    def test_blocked_close_is_bounded_and_prevents_restart(self):
        """Client close is owned by one retained cleanup worker."""
        block, entered, release = self._block()
        self.transport._client.close.side_effect = block
        self._bounded_stop()
        self.assertTrue(entered.wait(1))
        cleanup = self.transport._cleanup
        self._bounded_stop()
        self.assertIs(self.transport._cleanup, cleanup)
        with self.assertRaises(RuntimeError):
            self.transport.start()
        release.set()
        cleanup.join(1)
        self.transport._client.close.assert_called_once()

    def test_close_errors_are_visible(self):
        """Cleanup exceptions must be logged."""
        self.transport._client.close.side_effect = RuntimeError("close failed")
        self.transport.stop()
        self.logs.log_trace.assert_called_once()

    def test_idle_writer_exits_without_sentinel(self):
        """An empty queue does not trap the writer after stop."""
        worker = self._worker(self.transport._writer_loop)
        self.transport._stop.set()
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.transport._q.empty())

    def test_stop_during_pending_and_normal_reads(self):
        """Reads released after stop cannot dispatch their returned batches."""
        for pending in (True, False):
            with self.subTest(pending=pending):
                self.transport._stop.clear()
                block, entered, release = self._block()

                def read(*args, **kwargs):
                    """Hold the response until stop has been requested."""
                    block()
                    return [("s", [(b"1-0", {b"c": b"Msg", b"d": b"body"})])]

                self.transport._client.xreadgroup.side_effect = read
                with patch.object(self.transport, "_dispatch") as dispatch:
                    if pending:
                        worker = self._worker(self.transport._drain_pending)
                    else:
                        with patch.object(self.transport, "_drain_pending"):
                            worker = self._worker(self.transport._reader_loop)
                            self.assertTrue(entered.wait(1))
                    self.assertTrue(entered.wait(1))
                    self.transport._stop.set()
                    release.set()
                    worker.join(1)
                    self.assertFalse(worker.is_alive())
                    dispatch.assert_not_called()

    def test_retry_wait_is_interruptible_without_control_requeue(self):
        """Stop interrupts backoff without another write or queued retry."""
        entered = self.threading.Event()
        original_wait = self.transport._stop.wait

        def wait(delay):
            """Expose the event wait without relying on backoff timing."""
            entered.set()
            return original_wait(5)

        self.transport._client.xadd.side_effect = RuntimeError("offline")
        self.transport.publish("s", b"AdminX", b"body")
        with patch.object(self.transport._stop, "wait", side_effect=wait):
            worker = self._worker(self.transport._writer_loop)
            try:
                self.assertTrue(entered.wait(1))
            finally:
                self.transport._stop.set()
                worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.transport._client.xadd.assert_called_once()
        self.assertTrue(self.transport._q.empty())

    def test_restart_discards_stale_work_and_preserves_old_client(self):
        """Fresh workers publish fresh work without old cleanup touching their client."""
        import redis

        old = self.transport._client
        self.transport.publish("s", b"Msg", b"stale")
        self.transport.stop()
        fresh = fakeredis.FakeRedis()
        with patch.object(redis.Redis, "from_url", return_value=fresh):
            self.assertTrue(self.transport.start())
        self.workers.extend([self.transport._writer, self.transport._reader])
        try:
            self.assertFalse(self.transport.start())
            self.transport.publish("out", b"Msg", b"fresh")
            deadline = time.monotonic() + 1
            while fresh.xlen("out") == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual([row[1][b"d"] for row in fresh.xrange("out")], [b"fresh"])
            self.assertEqual(fresh.xlen("s"), 0)
            old.close.assert_called_once()
        finally:
            self.transport.stop()

    def test_server_setup_only_after_fresh_success(self):
        """Duplicate and rejected starts do not rerun PSYNC or initial setup."""
        bus = RedisServerBus(MagicMock())
        with patch.object(bus, "send_AdminServer2Portal") as send:
            for outcome in (False, RuntimeError("survivor"), True):
                with self.subTest(outcome=outcome):
                    with patch.object(bus._transport, "start", side_effect=[outcome]):
                        if isinstance(outcome, Exception):
                            with self.assertRaises(RuntimeError):
                                bus.start_bus()
                        else:
                            bus.start_bus()
                    self.assertEqual(send.call_count, int(outcome is True))
                    self.assertEqual(
                        bus.factory.server.run_initial_setup.call_count, int(outcome is True)
                    )

    def test_failed_restart_client_is_cleaned_up(self):
        """A failed restart gives its newly created client its own cleanup."""
        import redis

        self.transport.stop()
        failed = MagicMock()
        failed.ping.side_effect = redis.exceptions.ConnectionError("offline")
        with (
            patch.object(redis.Redis, "from_url", return_value=failed),
            patch.object(redis_bus.clock, "stop_loop"),
        ):
            self.assertFalse(self.transport.start())
        self.transport.stop()
        failed.close.assert_called_once()

    def test_stopping_caller_joins_only_cleanup_with_remaining_budget(self):
        """Both workers consume a single caller-side join budget."""
        block, entered, release = self._block()
        self.transport._reader = self._worker(block)
        self.assertTrue(entered.wait(1))
        caller = self.threading.get_ident()
        joins = []
        original_join = self.threading.Thread.join

        def join(worker, timeout=None):
            """Record only waits performed by the stopping caller."""
            if self.threading.get_ident() == caller:
                joins.append((worker, timeout))
            return original_join(worker, timeout)

        with patch.object(self.threading.Thread, "join", join):
            self.transport.stop()
        self.assertEqual(len(joins), 1)
        self.assertIs(joins[0][0], self.transport._cleanup)
        self.assertGreaterEqual(joins[0][1], 0)
        self.assertLessEqual(joins[0][1], 0.05)
        with self.assertRaises(RuntimeError):
            self.transport.start()


class TestBusFixtureCleanup(SimpleTestCase):
    """Fixture cleanup owns its callbacks without touching ambient work."""

    def test_cleanup_cancels_owned_work_before_restoring_globals(self):
        """Only the fixture coroutine sees cancellation; its timer never fires."""
        original = evennia.SERVER_SESSION_HANDLER
        pending = [(MagicMock(), (), {})]
        ambient = asyncio.new_event_loop()
        ambient_task = ambient.create_task(asyncio.sleep(100))
        observed = []
        with patch.object(clock, "_pending_when_running", pending):
            resources = _BusTestResources()
            self.addCleanup(resources.close)
            try:
                owned = object()
                evennia.SERVER_SESSION_HANDLER = owned
                handle = clock.call_later(0, lambda: observed.append("timer"))

                async def waiting():
                    """Observe which globals are active during cancellation."""
                    try:
                        await asyncio.sleep(100)
                    finally:
                        observed.append(evennia.SERVER_SESSION_HANDLER)

                task = resources.loop.create_task(waiting())
                # The scheduling loop stays stopped while reader threads exist.
                handle.cancel()
                resources.loop.run_until_complete(asyncio.sleep(0))
                uncanceled = clock.call_later(0, lambda: observed.append("late timer"))
                resources.close()
                self.assertTrue(task.cancelled())
                self.assertTrue(uncanceled.cancelled())
                self.assertEqual(observed, [owned])
                self.assertIs(evennia.SERVER_SESSION_HANDLER, original)
                self.assertIs(clock._pending_when_running, pending)
                self.assertEqual(len(pending), 1)
                pending[0][0].assert_not_called()
                self.assertFalse(ambient_task.done())
            finally:
                resources.close()
                ambient_task.cancel()
                ambient.run_until_complete(asyncio.gather(ambient_task, return_exceptions=True))
                ambient.close()

    def test_stop_failure_does_not_skip_other_cleanup(self):
        """Stop failures stay visible while other resources and globals restore."""
        original_get_loop = clock._get_loop
        original_handler = evennia.SERVER_SESSION_HANDLER
        resources = _BusTestResources()
        evennia.SERVER_SESSION_HANDLER = object()
        first, second = MagicMock(), MagicMock()
        first._transport.stop.side_effect = RuntimeError("stop failed")
        for name in ("_writer", "_reader", "_cleanup"):
            setattr(first._transport, name, None)
            setattr(second._transport, name, None)
        resources.buses.extend([first, second])
        with self.assertRaises(ExceptionGroup) as caught:
            resources.close()
        self.assertIn("stop failed", str(caught.exception.exceptions[0]))
        second._transport.stop.assert_called_once()
        self.assertTrue(resources.loop.is_closed())
        self.assertIs(clock._get_loop, original_get_loop)
        self.assertIs(evennia.SERVER_SESSION_HANDLER, original_handler)

    def test_coroutine_cleanup_error_is_reported(self):
        """Cancellation must not hide a failure raised by coroutine cleanup."""
        original = clock._get_loop
        resources = _BusTestResources()

        async def failing_cleanup():
            """Fail during cancellation rather than silently returning an error."""
            try:
                await asyncio.sleep(100)
            finally:
                raise RuntimeError("cleanup failed")

        resources.loop.create_task(failing_cleanup())
        resources.loop.run_until_complete(asyncio.sleep(0))
        with self.assertRaises(ExceptionGroup) as caught:
            resources.close()
        self.assertIn("cleanup failed", str(caught.exception.exceptions[0]))
        self.assertIs(clock._get_loop, original)
        self.assertTrue(resources.loop.is_closed())

    def test_setup_failure_still_releases_registered_resources(self):
        """An exception after resource acquisition cannot leak fixture globals."""
        from unittest import TestResult

        original = evennia.SERVER_SESSION_HANDLER

        class BrokenFixture(SimpleTestCase):
            """Model a fixture that fails during service construction."""

            def setUp(self):
                """Register cleanup before changing process state."""
                self.resources = _BusTestResources()
                self.addCleanup(self.resources.close)
                evennia.SERVER_SESSION_HANDLER = object()
                raise RuntimeError("setup failed")

            def runTest(self):
                """Fail if the runner incorrectly enters the test body."""
                raise AssertionError("setup should prevent the test body")

        fixture = BrokenFixture()
        result = TestResult()
        fixture.run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("setup failed", result.errors[0][1])
        self.assertIs(evennia.SERVER_SESSION_HANDLER, original)
        self.assertTrue(fixture.resources.loop.is_closed())

    def test_surviving_reader_prevents_driving_private_loop(self):
        """A terminal stop failure must not race the reader against loop cleanup."""
        import threading
        from types import SimpleNamespace

        resources = _BusTestResources()
        release = threading.Event()
        reader = threading.Thread(target=release.wait, daemon=True)
        reader.start()
        resources.buses.append(
            SimpleNamespace(
                _transport=SimpleNamespace(
                    stop=MagicMock(),
                    _reader=reader,
                    _writer=None,
                    _cleanup=None,
                )
            )
        )
        task = resources.loop.create_task(asyncio.sleep(100))
        try:
            with (
                patch.object(resources.loop, "run_until_complete") as drive,
                patch.object(resources.loop, "close"),
            ):
                with self.assertRaises(ExceptionGroup):
                    resources.close()
                drive.assert_not_called()
        finally:
            release.set()
            reader.join(1)
            resources.loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
            resources.loop.close()
