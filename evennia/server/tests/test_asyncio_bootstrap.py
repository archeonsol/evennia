"""Tests for the asyncio Portal/Server bootstrap (T3 S8/S9)."""

import asyncio
import sys
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings


class BootstrapCmdlineTest(SimpleTestCase):
    @patch("evennia.server.asyncio_bootstrap.os.name", "posix")
    def test_build_cmdline_uses_python_entrypoints(self):
        from evennia.server.asyncio_bootstrap import build_cmdline

        portal_cmd, server_cmd = build_cmdline(
            portal_py_file="/evennia/server/portal/portal.py",
            server_py_file="/evennia/server/server.py",
            portal_pidfile="/game/server/portal.pid",
            server_pidfile="/game/server/server.pid",
        )
        self.assertEqual(portal_cmd[0], sys.executable)
        self.assertEqual(portal_cmd[1], "/evennia/server/portal/portal.py")
        self.assertIn("--pidfile", portal_cmd)
        self.assertEqual(server_cmd[1], "/evennia/server/server.py")

    @override_settings(EVENNIA_ASYNCIO_BOOTSTRAP=True)
    @patch("evennia.server.evennia_launcher.os.name", "posix")
    @patch("evennia.server.evennia_launcher.PORTAL_PY_FILE", "/p/portal.py")
    @patch("evennia.server.evennia_launcher.SERVER_PY_FILE", "/p/server.py")
    @patch("evennia.server.evennia_launcher.PORTAL_PIDFILE", "/game/portal.pid")
    @patch("evennia.server.evennia_launcher.SERVER_PIDFILE", "/game/server.pid")
    @patch("evennia.server.evennia_launcher.PPROFILER_LOGFILE", "/game/portal.prof")
    @patch("evennia.server.evennia_launcher.SPROFILER_LOGFILE", "/game/server.prof")
    def test_launcher_uses_bootstrap_when_flag_set(self):
        from evennia.server import evennia_launcher

        pcmd, scmd = evennia_launcher._get_twistd_cmdline(False, False)
        self.assertEqual(pcmd[1], "/p/portal.py")
        self.assertEqual(scmd[1], "/p/server.py")
        self.assertIn("--pidfile", pcmd)
        self.assertIn("--pidfile", scmd)
        self.assertNotIn("twistd", pcmd[0])


class SignalShutdownCoordinatorTest(SimpleTestCase):
    """Process signals latch through startup and activate only after readiness."""

    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def test_signal_during_cold_nested_loop_waits_for_ready(self):
        from evennia.server.asyncio_bootstrap import _SignalShutdownCoordinator

        coordinator = _SignalShutdownCoordinator(self.loop, portal_mode=False)
        service = MagicMock()

        async def finish():
            return None

        service.request_shutdown.side_effect = lambda **_kwargs: self.loop.create_task(finish())
        self.loop.call_soon(coordinator.handle_signal, 15)
        self.loop.run_until_complete(asyncio.sleep(0))
        service.request_shutdown.assert_not_called()

        with patch("evennia.server.asyncio_bootstrap.clock.call_later") as emergency:
            coordinator.mark_ready(service)
            self.loop.run_until_complete(asyncio.sleep(0))

        service.request_shutdown.assert_called_once_with(mode="reload", _reactor_stopping=True)
        emergency.assert_called_once()

    def test_startup_failure_discards_latched_graceful_request(self):
        from evennia.server.asyncio_bootstrap import _SignalShutdownCoordinator

        coordinator = _SignalShutdownCoordinator(self.loop, portal_mode=False)
        service = MagicMock()
        coordinator.handle_signal(15)
        coordinator.mark_failed()
        coordinator.mark_ready(service)
        service.request_shutdown.assert_not_called()

    @patch("evennia.server.asyncio_bootstrap.clock.stop_loop")
    def test_second_signal_forces_stop_without_duplicate_task(self, stop_loop):
        from evennia.server.asyncio_bootstrap import _SignalShutdownCoordinator

        coordinator = _SignalShutdownCoordinator(self.loop, portal_mode=False)
        coordinator.handle_signal(2)
        coordinator.handle_signal(15)

        self.assertTrue(coordinator.forced)
        stop_loop.assert_called_once()

    @patch("evennia.server.asyncio_bootstrap.signal.signal")
    def test_raw_handlers_only_post_both_signals(self, signal_install):
        from evennia.server import asyncio_bootstrap

        coordinator = MagicMock()
        asyncio_bootstrap._install_signal_handlers(coordinator)
        handlers = {call.args[0]: call.args[1] for call in signal_install.call_args_list}

        handlers[asyncio_bootstrap.signal.SIGINT](asyncio_bootstrap.signal.SIGINT, None)
        coordinator.post_signal.assert_called_once_with(asyncio_bootstrap.signal.SIGINT)
        if hasattr(asyncio_bootstrap.signal, "SIGTERM"):
            handlers[asyncio_bootstrap.signal.SIGTERM](asyncio_bootstrap.signal.SIGTERM, None)
            coordinator.post_signal.assert_called_with(asyncio_bootstrap.signal.SIGTERM)


class BootstrapRunTest(SimpleTestCase):
    def setUp(self):
        """Preserve loop globals changed by the process bootstrap."""
        super().setUp()
        from evennia.utils import clock

        self._saved_bound_loop = clock._main_loop
        self._saved_bound_thread = clock._loop_thread_id
        try:
            self._saved_event_loop = asyncio.get_event_loop()
        except RuntimeError:
            self._saved_event_loop = None

    def tearDown(self):
        """Restore the runner's loop after bootstrap closes its own loop."""
        from evennia.utils import clock

        clock._main_loop = self._saved_bound_loop
        clock._loop_thread_id = self._saved_bound_thread
        asyncio.set_event_loop(self._saved_event_loop)
        super().tearDown()

    def test_run_bootstrap_starts_and_stops_application(self):
        from evennia.server.asyncio_bootstrap import run_bootstrap

        loop = MagicMock()
        loop.is_closed.return_value = False
        loop.is_running = True
        service = MagicMock()
        service.running = True

        # run_bootstrap does a local ``import evennia``, which shadows any patch
        # of the bootstrap module's ``evennia`` attribute; patch the real module
        # attributes the local import resolves to.
        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap.asyncio.set_event_loop"),
            patch("evennia.server.asyncio_bootstrap.clock.bind_loop"),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile"),
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
        ):
            run_bootstrap(portal_mode=True, argv=[])

        service.privilegedStartService.assert_called_once()
        service.startService.assert_called_once()
        service.stopService.assert_called_once()
        loop.run_forever.assert_called_once()

    def test_lifecycle_runs_in_order(self):
        # The count-based asserts above pass even if the calls were reordered.
        # A running loop must start after privileged/start and stop only after
        # run_forever returns; record the real order and pin it.
        from evennia.server.asyncio_bootstrap import run_bootstrap

        order = []
        loop = MagicMock()
        loop.is_closed.return_value = False
        loop.run_forever.side_effect = lambda: order.append("run_forever")
        service = MagicMock()
        service.running = True
        service.privilegedStartService.side_effect = lambda: order.append("privileged")
        service.startService.side_effect = lambda: order.append("start")
        service.stopService.side_effect = lambda: order.append("stop")

        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap.asyncio.set_event_loop"),
            patch("evennia.server.asyncio_bootstrap.clock.bind_loop"),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile"),
            patch(
                "evennia.server.asyncio_bootstrap.clock.run_shutdown_hooks",
                side_effect=lambda **kwargs: order.append(("hooks", kwargs)),
            ),
            patch(
                "evennia.server.asyncio_bootstrap.clock.cancel_pending_tasks",
                side_effect=lambda _loop: order.append("cancel"),
            ),
            patch(
                "evennia.server.asyncio_bootstrap.clock.shutdown_default_executor",
                side_effect=lambda: order.append("executor"),
            ),
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
        ):
            run_bootstrap(portal_mode=True, argv=[])

        self.assertEqual(
            order,
            [
                "privileged",
                "start",
                "run_forever",
                ("hooks", {"shutdown_executor": False}),
                "stop",
                "cancel",
                "executor",
            ],
        )

    def test_forced_signal_during_nested_start_skips_main_loop(self):
        """A swallowed cold-start stop cannot be lost before run_forever."""
        from evennia.server.asyncio_bootstrap import run_bootstrap

        loop = asyncio.new_event_loop()
        service = MagicMock()
        service.running = True
        coordinator = None
        run_forever_calls = 0
        original_run_forever = loop.run_forever

        def install(value):
            nonlocal coordinator
            coordinator = value

        def counted_run_forever():
            nonlocal run_forever_calls
            run_forever_calls += 1
            return original_run_forever()

        def cold_start():
            async def bind_listener():
                loop.call_soon(coordinator.handle_signal, 2)
                loop.call_soon(coordinator.handle_signal, 15)
                await asyncio.Event().wait()

            task = loop.create_task(bind_listener())
            with self.assertRaisesRegex(RuntimeError, "Event loop stopped"):
                loop.run_until_complete(task)
            task.cancel()
            loop.run_until_complete(asyncio.gather(task, return_exceptions=True))

        service.privilegedStartService.side_effect = cold_start
        loop.run_forever = counted_run_forever

        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers", side_effect=install),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile"),
            patch("evennia.server.asyncio_bootstrap.clock.run_shutdown_hooks"),
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
        ):
            run_bootstrap(portal_mode=True, argv=[])

        self.assertEqual(run_forever_calls, 2)
        self.assertTrue(coordinator.forced)
        service.stopService.assert_called_once()

    def test_pending_runtime_root_is_settled_before_loop_close(self):
        from evennia.server.asyncio_bootstrap import run_bootstrap
        from evennia.utils import clock

        loop = asyncio.new_event_loop()
        service = MagicMock()
        service.running = True

        async def pending():
            await asyncio.Event().wait()

        task = clock._create_runtime_task(loop, pending(), "test")
        loop.call_soon(loop.stop)

        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile"),
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
        ):
            run_bootstrap(portal_mode=True, argv=[])

        self.assertTrue(task.cancelled())
        self.assertTrue(loop.is_closed())

    def test_partial_startup_failure_preserves_error_and_cleans_up(self):
        from evennia.server.asyncio_bootstrap import run_bootstrap

        loop = MagicMock()
        loop.is_closed.return_value = False
        service = MagicMock()
        service.privilegedStartService.side_effect = RuntimeError("listener failed")

        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap.asyncio.set_event_loop"),
            patch("evennia.server.asyncio_bootstrap.clock.bind_loop"),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile") as mock_remove,
            patch("evennia.server.asyncio_bootstrap.clock.cancel_pending_tasks") as mock_cancel,
            patch(
                "evennia.server.asyncio_bootstrap.clock.shutdown_default_executor"
            ) as mock_executor,
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "listener failed"):
                run_bootstrap(portal_mode=True, argv=[])

        service.stopService.assert_called_once()
        mock_cancel.assert_called_once_with(loop)
        mock_executor.assert_called_once()
        mock_remove.assert_called_once()
        loop.close.assert_called_once()

    def test_startservice_failure_preserves_error_and_cleans_up(self):
        from evennia.server.asyncio_bootstrap import run_bootstrap

        loop = MagicMock()
        loop.is_closed.return_value = False
        service = MagicMock()
        service.startService.side_effect = RuntimeError("children failed")

        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap.asyncio.set_event_loop"),
            patch("evennia.server.asyncio_bootstrap.clock.bind_loop"),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile") as mock_remove,
            patch("evennia.server.asyncio_bootstrap.clock.cancel_pending_tasks") as mock_cancel,
            patch(
                "evennia.server.asyncio_bootstrap.clock.shutdown_default_executor"
            ) as mock_executor,
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "children failed"):
                run_bootstrap(portal_mode=True, argv=[])

        service.stopService.assert_called_once()
        mock_cancel.assert_called_once_with(loop)
        mock_executor.assert_called_once()
        mock_remove.assert_called_once()
        loop.close.assert_called_once()

    def test_startup_failure_settles_portal_hook_task_before_stopservice(self):
        from evennia.server.asyncio_bootstrap import run_bootstrap

        order = []
        loop = asyncio.new_event_loop()
        service = MagicMock()
        service.running = True
        service.startService.side_effect = RuntimeError("children failed")
        service.stopService.side_effect = lambda: order.append("stop")

        async def cleanup():
            order.append("cleanup")

        def hooks(**_kwargs):
            service._shutdown_task = loop.create_task(cleanup())

        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile"),
            patch(
                "evennia.server.asyncio_bootstrap.clock.run_shutdown_hooks",
                side_effect=hooks,
            ),
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "children failed"):
                run_bootstrap(portal_mode=True, argv=[])

        self.assertEqual(order[:2], ["cleanup", "stop"])

    def test_initialization_failure_preserves_error_and_cleans_up(self):
        from evennia.server.asyncio_bootstrap import run_bootstrap

        loop = MagicMock()
        loop.is_closed.return_value = False

        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap.asyncio.set_event_loop"),
            patch("evennia.server.asyncio_bootstrap.clock.bind_loop"),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile") as mock_remove,
            patch("evennia.server.asyncio_bootstrap.clock.cancel_pending_tasks") as mock_cancel,
            patch(
                "evennia.server.asyncio_bootstrap.clock.shutdown_default_executor"
            ) as mock_executor,
            patch("evennia._LOADED", False, create=True),
            patch("evennia._init", side_effect=RuntimeError("init failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "init failed"):
                run_bootstrap(portal_mode=True, argv=[])

        mock_cancel.assert_called_once_with(loop)
        mock_executor.assert_called_once()
        mock_remove.assert_called_once()
        loop.close.assert_called_once()

    def test_stopservice_failure_is_logged_and_siblings_still_run(self):
        from evennia.server.asyncio_bootstrap import run_bootstrap

        loop = MagicMock()
        loop.is_closed.return_value = False
        service = MagicMock()
        service.running = True
        service.stopService.side_effect = RuntimeError("boom")

        # run_bootstrap does a local ``import evennia`` (line ~133), which
        # shadows any patch of the bootstrap module's ``evennia`` attribute, so
        # patch the real module attributes the local import resolves to.
        with (
            patch("evennia.server.asyncio_bootstrap.asyncio.new_event_loop", return_value=loop),
            patch("evennia.server.asyncio_bootstrap.asyncio.set_event_loop"),
            patch("evennia.server.asyncio_bootstrap.clock.bind_loop"),
            patch("evennia.server.asyncio_bootstrap._install_signal_handlers"),
            patch("evennia.server.asyncio_bootstrap._setup_process_logging"),
            patch("evennia.server.asyncio_bootstrap._write_pidfile"),
            patch("evennia.server.asyncio_bootstrap._remove_pidfile") as mock_remove,
            patch("evennia._LOADED", True, create=True),
            patch("evennia.EVENNIA_PORTAL_SERVICE", service, create=True),
            patch("evennia.utils.logger.log_trace") as mock_trace,
        ):
            run_bootstrap(portal_mode=True, argv=[])

        # a failing stopService must be logged, not silently swallowed
        self.assertTrue(mock_trace.called)
        # and sibling teardown (pidfile removal, loop close) must still run
        mock_remove.assert_called_once()
        loop.close.assert_called_once()
