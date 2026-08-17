"""Native asyncio process bootstrap for Portal/Server (T3 S8/S9).

Creates and owns the process asyncio loop, starts the service tree via
:mod:`evennia.server.service_registry`, and runs ``loop.run_forever()`` until
graceful shutdown.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from evennia.utils import clock


def _parse_bootstrap_args(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pidfile")
    parser.add_argument("--nodaemon", action="store_true")
    parser.add_argument("--profiler", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--savestats", action="store_true")
    return parser.parse_known_args(argv)


def _write_pidfile(path):
    if not path:
        return
    pid_dir = os.path.dirname(os.path.abspath(path))
    if pid_dir:
        os.makedirs(pid_dir, exist_ok=True)
    with open(path, "w", encoding="ascii") as f:
        f.write(str(os.getpid()))


def _remove_pidfile(path):
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _setup_process_logging(portal_mode, nodaemon):
    """Attach the Portal/Server file log observer (twistd ``--logger`` equivalent)."""
    from django.conf import settings
    from twisted.logger import globalLogPublisher

    from evennia.utils import logger

    if portal_mode:
        observer_factory = logger.GetPortalLogObserver
        log_file = settings.PORTAL_LOG_FILE
        day_rotation = settings.PORTAL_LOG_DAY_ROTATION
        max_size = settings.PORTAL_LOG_MAX_SIZE
    else:
        observer_factory = logger.GetServerLogObserver
        log_file = settings.SERVER_LOG_FILE
        day_rotation = settings.SERVER_LOG_DAY_ROTATION
        max_size = settings.SERVER_LOG_MAX_SIZE

    logfile = logger.WeeklyLogFile(
        os.path.basename(log_file),
        os.path.dirname(log_file),
        day_rotation=day_rotation,
        max_size=max_size,
    )
    globalLogPublisher.addObserver(observer_factory()(logfile))
    if nodaemon:
        logger.prune_rotated_logs(force=True)


class _SignalShutdownCoordinator:
    """Latch process signals and activate one service-owned shutdown task."""

    def __init__(self, loop, *, portal_mode):
        self.loop = loop
        self.portal_mode = portal_mode
        self.state = "starting"
        self.service = None
        self.requested = False
        self.signal_count = 0
        self.task = None
        self.emergency_handle = None
        self.forced = False

    def post_signal(self, signum):
        """Post raw signal input onto the process loop without mutating state."""

        try:
            self.loop.call_soon_threadsafe(self.handle_signal, signum)
        except RuntimeError:
            pass

    def handle_signal(self, signum):
        """Handle one signal on the owner loop."""

        self.signal_count += 1
        if self.signal_count > 1:
            from evennia.utils import logger

            self.forced = True
            logger.log_warn("Second interrupt received; stopping immediately.")
            clock.stop_loop()
            return

        self.requested = True
        if self.state == "ready":
            self._activate()

    def mark_ready(self, service):
        """Publish a fully started service and activate any latched signal."""

        if self.state == "failed":
            return
        self.service = service
        self.state = "ready"
        if self.requested and not self.forced:
            self._activate()

    def mark_failed(self):
        """Prevent a startup-time signal from fabricating graceful completion."""

        self.state = "failed"
        self.service = None

    def _activate(self):
        """Request the exact service task and arm its emergency backstop."""

        if self.task is not None or self.service is None:
            return
        try:
            if self.portal_mode:
                self.task = self.service.shutdown(
                    _reactor_stopping=True,
                    _stop_server=True,
                    _stop_loop=True,
                )
            else:
                self.task = self.service.request_shutdown(mode="reload", _reactor_stopping=True)
        except Exception:
            from evennia.utils import logger

            self.forced = True
            logger.log_trace("could not start signal shutdown task")
            clock.stop_loop()
            return

        from django.conf import settings

        emergency = float(getattr(settings, "SERVER_SHUTDOWN_EMERGENCY_TIMEOUT", 30.0))
        self.emergency_handle = clock.call_later(emergency, self._emergency_stop)
        self.task.add_done_callback(self._task_settled)

    def _task_settled(self, _task):
        """Disarm the emergency deadline after normal task settlement."""

        if self.emergency_handle is not None:
            self.emergency_handle.cancel()
            self.emergency_handle = None

    def _emergency_stop(self):
        """Force loop termination when graceful work exceeds its deadline."""

        from evennia.utils import logger

        self.forced = True
        logger.log_warn("Graceful shutdown timed out; stopping immediately.")
        clock.stop_loop()


def _install_signal_handlers(coordinator):
    """Route raw SIGINT/SIGTERM callbacks onto the owner loop."""

    def _handle_signal(signum, _frame):
        coordinator.post_signal(signum)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)


def build_cmdline(
    *,
    portal_py_file,
    server_py_file,
    portal_pidfile=None,
    server_pidfile=None,
    portal_profiler_log=None,
    server_profiler_log=None,
    pprofiler=False,
    sprofiler=False,
):
    """Return ``(portal_argv, server_argv)`` for asyncio bootstrap launches."""
    python = sys.executable
    portal_cmd = [python, portal_py_file]
    server_cmd = [python, server_py_file]

    if os.name != "nt":
        if portal_pidfile:
            portal_cmd.extend(["--pidfile", portal_pidfile])
        if server_pidfile:
            server_cmd.extend(["--pidfile", server_pidfile])

    if pprofiler and portal_profiler_log:
        portal_cmd.extend(
            ["--savestats", "--profiler=cprofile", f"--profile={portal_profiler_log}"]
        )
    if sprofiler and server_profiler_log:
        server_cmd.extend(
            ["--savestats", "--profiler=cprofile", f"--profile={server_profiler_log}"]
        )

    return portal_cmd, server_cmd


def run_bootstrap(*, portal_mode: bool, argv=None):
    """Start an Evennia Portal or Server process without twistd."""
    args, _unknown = _parse_bootstrap_args(argv)
    _write_pidfile(args.pidfile)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    clock.bind_loop(loop)
    coordinator = _SignalShutdownCoordinator(loop, portal_mode=portal_mode)
    _install_signal_handlers(coordinator)
    service = None
    start_attempted = False
    startup_complete = False
    try:
        import evennia

        if not getattr(evennia, "_LOADED", False):
            evennia._init(portal_mode=portal_mode)

        if portal_mode:
            service = evennia.EVENNIA_PORTAL_SERVICE
        else:
            service = evennia.EVENNIA_SERVER_SERVICE

        if "test" not in sys.argv:
            _setup_process_logging(portal_mode, args.nodaemon)

        # privilegedStartService registers listeners; startService starts children.
        start_attempted = True
        service.privilegedStartService()
        service.startService()
        startup_complete = True
        coordinator.mark_ready(service)
        if not coordinator.forced:
            loop.run_forever()
    except BaseException:
        if not startup_complete:
            coordinator.mark_failed()
        raise
    finally:
        from evennia.utils import logger

        try:
            clock.run_shutdown_hooks(shutdown_executor=False)
        except Exception:
            logger.log_trace("error running shutdown hooks")
        shutdown_task = getattr(service, "_shutdown_task", None)
        if (
            isinstance(shutdown_task, asyncio.Task)
            and not shutdown_task.done()
            and not coordinator.forced
        ):
            try:
                loop.run_until_complete(asyncio.gather(shutdown_task, return_exceptions=True))
            except Exception:
                logger.log_trace("error settling service shutdown task")
        try:
            if service is not None and (start_attempted or service.running):
                service.stopService()
        except Exception:
            # log rather than swallow, but keep going: sibling teardown
            # (pidfile removal, loop close) must still run
            logger.log_trace("error during service.stopService() on shutdown")
        try:
            clock.cancel_pending_tasks(loop)
        except Exception:
            logger.log_trace("error settling pending tasks on shutdown")
        try:
            clock.shutdown_default_executor()
        except Exception:
            logger.log_trace("error shutting down the worker executor")
        _remove_pidfile(args.pidfile)
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            logger.log_trace("error closing the event loop on shutdown")


def run_portal(argv=None):
    run_bootstrap(portal_mode=True, argv=argv)


def run_server(argv=None):
    run_bootstrap(portal_mode=False, argv=argv)
