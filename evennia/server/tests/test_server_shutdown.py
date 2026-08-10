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
        from evennia.typeclasses import jsonb_handler
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

        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with (
                    patch("evennia.server.service.evennia") as mock_evennia,
                    patch.object(
                        jsonb_handler,
                        "_write_locked_document",
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

                    asyncio.run(service.shutdown(mode="reload"))

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
