"""Tests for server shutdown timing and reload hook trimming."""

import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import django
from django.test import SimpleTestCase, override_settings

from evennia.utils.test_resources import BaseEvenniaTest

django.setup()


class ServerShutdownDelayTest(SimpleTestCase):
    def _service(self):
        from evennia.server.service import EvenniaServerService

        with patch.object(EvenniaServerService, "sqlite3_prep"):
            return EvenniaServerService()

    @patch("evennia.scripts.monitorhandler.MONITOR_HANDLER")
    @patch("evennia.server.service.clock")
    def test_shutdown_reload_with_immediate_result_portal_sync(self, mock_clock, _monitor):
        from evennia.server.service_registry import IMMEDIATE_RESULT
        from evennia.utils import clock as real_clock

        service = self._service()
        service.stall_watchdog = None
        service.system_driver = None
        service.maintenance_task = None
        mock_clock.maybe_await = real_clock.maybe_await

        with patch("evennia.server.service.evennia") as mock_evennia:
            mock_evennia.ObjectDB.get_all_cached_instances.return_value = []
            mock_evennia.AccountDB.get_all_cached_instances.return_value = []
            mock_evennia.ScriptDB.get_all_cached_instances.return_value = []
            mock_evennia.ServerConfig.objects.conf = MagicMock()
            mock_evennia.gametime.runtime.return_value = 0
            mock_evennia.SESSION_HANDLER.all_sessions_portal_sync.return_value = IMMEDIATE_RESULT

            asyncio.run(service.shutdown(mode="reload"))

        mock_clock.call_later.assert_called_once_with(0, mock_clock.stop_loop)


class FinalSessionSyncOrderingTest(SimpleTestCase):
    """Late shutdown changes reach Portal before the process can stop."""

    def exercise_shutdown(self, mode, fail_snapshot=False):
        """Run the real shutdown body with an explicitly delayed Portal ACK."""
        from evennia.server import service as service_module
        from evennia.server.bus_result import TransportUnavailable

        with patch.object(service_module.EvenniaServerService, "sqlite3_prep"):
            service = service_module.EvenniaServerService()
        service.stall_watchdog = None
        service.system_driver = None
        service.maintenance_task = None
        service.portal_bus = MagicMock()
        state = {}
        order = []
        service.at_server_reload_stop = MagicMock(side_effect=lambda: order.append("reload_stop"))
        service.at_server_cold_stop = MagicMock(side_effect=lambda: order.append("cold_stop"))

        def stop_hook():
            state["hook"] = "final"
            order.append("stop_hook")

        async def drain_web():
            await asyncio.sleep(0)
            state["web"] = "drained"
            order.append("web_drain")

        service.at_server_stop = stop_hook
        service.web_root = MagicMock()
        service.web_root.empty_threadpool = drain_web

        with (
            patch.object(service_module, "evennia") as mock_evennia,
            patch.object(service_module.clock, "call_later") as schedule,
            patch.object(service_module.logger, "log_trace") as log,
            patch("evennia.scripts.monitorhandler.MONITOR_HANDLER"),
            patch("evennia.scripts.ondemandhandler.ON_DEMAND_HANDLER"),
            patch("evennia.typeclasses.attributes.flush_all_dirty"),
            patch("evennia.typeclasses.jsonb_handler.spool_remaining_dirty"),
        ):
            mock_evennia.ObjectDB.get_all_cached_instances.return_value = []
            mock_evennia.AccountDB.get_all_cached_instances.return_value = []
            mock_evennia.ScriptDB.get_all_cached_instances.return_value = []
            mock_evennia.gametime.runtime.return_value = 42

            async def run():
                sent = asyncio.Event()
                acknowledged = asyncio.Event()
                snapshots = []

                async def snapshot():
                    snapshots.append(dict(state))
                    order.append("snapshot")
                    sent.set()
                    await acknowledged.wait()
                    if fail_snapshot:
                        raise TransportUnavailable("Portal ACK lost")
                    order.append("ack")

                mock_evennia.SESSION_HANDLER.all_sessions_portal_sync = snapshot
                task = asyncio.create_task(service.shutdown(mode=mode))
                try:
                    await asyncio.wait_for(sent.wait(), 1)
                    self.assertEqual(snapshots, [{"hook": "final", "web": "drained"}])
                    self.assertLess(order.index("stop_hook"), order.index("snapshot"))
                    self.assertLess(order.index("web_drain"), order.index("snapshot"))
                    self.assertFalse(task.done())
                    schedule.assert_not_called()
                finally:
                    acknowledged.set()
                    await asyncio.wait_for(task, 1)

            asyncio.run(run())
            schedule.assert_called_once_with(0, service_module.clock.stop_loop)
            self.assertTrue(service.shutdown_complete)
            mock_evennia.ServerConfig.objects.conf.assert_any_call("runtime", 42)
            if fail_snapshot:
                log.assert_called_once_with(
                    "shutdown: final Portal snapshot unconfirmed; transition is unclean"
                )
            else:
                self.assertEqual(order[-1], "ack")
                log.assert_not_called()

    def test_reload_and_reset_snapshot_include_late_state(self):
        """Final hooks and web writes precede capture; ACK precedes loop stop."""
        for mode in ("reload", "reset"):
            with self.subTest(mode=mode):
                self.exercise_shutdown(mode)

    def test_failed_snapshot_still_finishes_shutdown_cleanup(self):
        """An unclean final handoff still schedules exit and saves runtime."""
        for mode in ("reload", "reset"):
            with self.subTest(mode=mode):
                self.exercise_shutdown(mode, fail_snapshot=True)


class ServerShutdownRequestTest(SimpleTestCase):
    """All production shutdown requests converge on one first-mode-wins task."""

    def setUp(self):
        super().setUp()
        from evennia.utils import clock

        self.clock = clock
        self._saved = (clock._main_loop, clock._loop_thread_id, clock._default_executor)
        clock._default_executor = None
        self.loop = asyncio.new_event_loop()
        clock.bind_loop(self.loop)

    def tearDown(self):
        self.clock.shutdown_default_executor()
        self.loop.close()
        (
            self.clock._main_loop,
            self.clock._loop_thread_id,
            self.clock._default_executor,
        ) = self._saved
        super().tearDown()

    def _service(self):
        from evennia.server.service import EvenniaServerService

        with patch.object(EvenniaServerService, "sqlite3_prep"):
            return EvenniaServerService()

    def test_first_mode_wins_and_web_pool_drains_before_domain_shutdown(self):
        service = self._service()
        order = []
        service.web_root = MagicMock()
        service.web_root.empty_threadpool = AsyncMock(side_effect=lambda: order.append("web"))
        service.shutdown = AsyncMock(
            side_effect=lambda *args, **kwargs: order.append((args, kwargs))
        )

        with patch("evennia.server.service.clock.stop_loop"):
            first = service.request_shutdown(mode="reset")
            second = service.request_shutdown(mode="shutdown")
            self.assertIs(first, second)
            self.loop.run_until_complete(first)

        self.assertEqual(order[0], "web")
        self.assertEqual(order[1], (("reset",), {"_reactor_stopping": True}))
        service.shutdown.assert_awaited_once()

    def test_ipc_modes_use_the_service_request_layer(self):
        from evennia.server import ipc_handlers_server
        from evennia.server.portal import amp

        service = MagicMock()
        with (
            patch.object(ipc_handlers_server, "evennia") as evennia,
            patch.object(
                ipc_handlers_server.ipc_schema,
                "parse_admin",
                return_value=(amp.DUMMYSESSION, amp.SRESET, {}),
            ),
        ):
            evennia.EVENNIA_SERVER_SERVICE = service
            ipc_handlers_server.receive_adminportal2server(b"ignored")

        service.request_shutdown.assert_called_once_with(mode="reset")

    @patch("evennia.scripts.monitorhandler.MONITOR_HANDLER")
    @patch("evennia.server.service.clock")
    def test_shutdown_reload_with_async_portal_sync(self, mock_clock, _monitor):
        from evennia.utils import clock as real_clock

        service = self._service()
        service.stall_watchdog = None
        service.system_driver = None
        service.maintenance_task = None
        mock_clock.maybe_await = real_clock.maybe_await

        with patch("evennia.server.service.evennia") as mock_evennia:
            mock_evennia.ObjectDB.get_all_cached_instances.return_value = []
            mock_evennia.AccountDB.get_all_cached_instances.return_value = []
            mock_evennia.ScriptDB.get_all_cached_instances.return_value = []
            mock_evennia.ServerConfig.objects.conf = MagicMock()
            mock_evennia.gametime.runtime.return_value = 0
            mock_evennia.SESSION_HANDLER.all_sessions_portal_sync = AsyncMock()

            asyncio.run(service.shutdown(mode="reload"))

        mock_clock.call_later.assert_called_once_with(0, mock_clock.stop_loop)


class ServerAttributeRecoveryTest(BaseEvenniaTest):
    """Exercise the durable Attribute handoff across a simulated restart."""

    def _service(self):
        """Create a server service without changing SQLite pragmas."""
        from evennia.server.service import EvenniaServerService

        with patch.object(EvenniaServerService, "sqlite3_prep"):
            return EvenniaServerService()

    @patch("evennia.scripts.ondemandhandler.ON_DEMAND_HANDLER")
    @patch("evennia.scripts.monitorhandler.MONITOR_HANDLER")
    @patch("evennia.server.service.clock")
    def test_shutdown_spool_is_reclaimed_before_startup_hooks(
        self, mock_clock, _monitor, _ondemand
    ):
        """A failed final flush survives restart and precedes game hooks."""
        from evennia.server.service_registry import IMMEDIATE_RESULT
        from evennia.typeclasses import attributes
        from evennia.typeclasses.attributes import (
            AttributeHandler,
            discard_dirty_backends,
            flush_all_dirty,
        )
        from evennia.typeclasses.jsonb_handler import (
            AttributeUpdateUnavailable,
            JsonbAttributeBackend,
            spool_pending_count,
        )
        from evennia.utils import clock as real_clock

        flush_all_dirty()
        handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        handler.add("restart-state", {"revision": 1})
        service = self._service()
        service.stall_watchdog = None
        service.system_driver = None
        service.maintenance_task = None
        service.at_server_reload_stop = MagicMock()
        service.at_server_stop = MagicMock()
        mock_clock.maybe_await = real_clock.maybe_await
        io_loop = real_clock.get_bound_loop()
        if io_loop is None:
            saved_runtime = (
                real_clock._main_loop,
                real_clock._loop_thread_id,
                real_clock._default_executor,
            )
            real_clock._default_executor = None
            io_loop = asyncio.new_event_loop()
            real_clock.bind_loop(io_loop)

            def restore_runtime():
                io_loop.close()
                real_clock.shutdown_default_executor()
                (
                    real_clock._main_loop,
                    real_clock._loop_thread_id,
                    real_clock._default_executor,
                ) = saved_runtime

            self.addCleanup(restore_runtime)

        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with (
                    patch("evennia.server.service.evennia") as mock_evennia,
                    patch.object(
                        attributes,
                        "flush_all_dirty",
                        side_effect=RuntimeError("database unavailable"),
                    ),
                ):
                    mock_evennia.ObjectDB.get_all_cached_instances.return_value = []
                    mock_evennia.AccountDB.get_all_cached_instances.return_value = []
                    mock_evennia.ScriptDB.get_all_cached_instances.return_value = []
                    mock_evennia.ServerConfig.objects.conf = MagicMock()
                    mock_evennia.gametime.runtime.return_value = 0
                    mock_evennia.SESSION_HANDLER.all_sessions_portal_sync.return_value = (
                        IMMEDIATE_RESULT
                    )

                    io_loop.run_until_complete(service.shutdown(mode="reload"))

                self.assertEqual(spool_pending_count(), 1)
                discard_dirty_backends()
                restarted = AttributeHandler(self.obj1, JsonbAttributeBackend)
                with self.assertRaises(AttributeUpdateUnavailable):
                    restarted.get("restart-state")

                observed = []

                def startup_hook(hookname):
                    observed.append((hookname, restarted.get("restart-state")))

                service._call_start_stop = startup_hook
                with patch("evennia.jobs.queue.reclaim_jobs"):
                    service.at_server_init()

                self.assertEqual(spool_pending_count(), 0)
                self.assertEqual(observed, [("at_server_init", {"revision": 1})])
                document = (
                    type(self.obj1)
                    ._base_manager.filter(pk=self.obj1.pk)
                    .values_list("db_attrs", flat=True)
                    .get()
                )
                self.assertEqual(document["~"]["_d"]["restart-state"], {"revision": 1})


class AtPostPortalSyncReloadTest(SimpleTestCase):
    def _service(self):
        from evennia.server.service import EvenniaServerService

        with patch.object(EvenniaServerService, "sqlite3_prep"):
            return EvenniaServerService()

    @patch("evennia.scripts.ondemandhandler.ON_DEMAND_HANDLER")
    @patch("evennia.scripts.taskhandler.TASK_HANDLER")
    @patch("evennia.scripts.monitorhandler.MONITOR_HANDLER")
    def test_reload_skips_default_channels_and_warmup(self, monitor, task, ondemand):
        service = self._service()
        service.create_default_channels = MagicMock()

        with patch("evennia.server.service.evennia") as mock_evennia:
            mock_evennia.ScriptDB.objects.update_scripts_after_server_start = MagicMock()
            mock_evennia.ServerConfig.objects.conf = MagicMock()
            monitor.restore = MagicMock()
            task.load = MagicMock()
            task.create_delays = MagicMock()
            ondemand.load = MagicMock()
            service.at_post_portal_sync("reload")

        service.create_default_channels.assert_not_called()

    @patch("evennia.scripts.ondemandhandler.ON_DEMAND_HANDLER")
    @patch("evennia.scripts.taskhandler.TASK_HANDLER")
    @patch("evennia.scripts.monitorhandler.MONITOR_HANDLER")
    def test_cold_start_still_creates_default_channels(self, monitor, task, ondemand):
        service = self._service()
        service.create_default_channels = MagicMock()

        with patch("evennia.server.service.evennia") as mock_evennia:
            mock_evennia.ScriptDB.objects.update_scripts_after_server_start = MagicMock()
            mock_evennia.ServerConfig.objects.conf = MagicMock()
            monitor.restore = MagicMock()
            task.load = MagicMock()
            task.create_delays = MagicMock()
            ondemand.load = MagicMock()
            service.at_post_portal_sync("shutdown")

        service.create_default_channels.assert_called_once()


class RunInitHooksReloadTest(SimpleTestCase):
    def _service(self):
        from evennia.server.service import EvenniaServerService

        with patch.object(EvenniaServerService, "sqlite3_prep"):
            return EvenniaServerService()

    @patch("evennia.utils.reactor_watchdog.ReactorStallWatchdog")
    @patch("evennia.utils.systems.SystemDriver")
    @patch("evennia.utils.systems.load_system_modules")
    @patch("evennia.server.at_init_scheduler.run_cached_at_init_burst")
    @patch("evennia.hooks.lint.warn_at_startup")
    @patch("evennia.server.service.clock")
    def test_reload_skips_hook_lint(
        self, mock_clock, hook_lint, _burst, _load_modules, driver_cls, _watchdog_cls
    ):
        service = self._service()
        service.update_defaults = MagicMock()
        service.at_server_init = MagicMock()
        service.at_server_reload_start = MagicMock()
        service.at_server_start = MagicMock()
        service.maintenance_task = MagicMock(running=False)
        service.system_driver = None
        service.stall_watchdog = None
        mock_clock.looping.return_value = MagicMock()
        driver_cls.return_value.start = MagicMock()

        with patch("evennia.server.service.evennia") as mock_evennia:
            mock_evennia.GLOBAL_SCRIPTS.start = MagicMock()
            service.run_init_hooks("reload")

        hook_lint.assert_not_called()
