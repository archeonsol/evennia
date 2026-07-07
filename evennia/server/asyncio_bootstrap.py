"""Native asyncio process bootstrap for Portal/Server (T3 S8/S9).

Replaces ``twistd`` as the Portal/Server entrypoint when
``settings.EVENNIA_ASYNCIO_BOOTSTRAP`` is enabled. Creates and owns the process
asyncio loop, starts the ``Application`` service tree, and runs
``loop.run_forever()`` until graceful shutdown.

Game hot paths use :mod:`evennia.utils.clock`, not ``reactor`` directly.
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


def _install_signal_handlers(portal_mode):
    """SIGINT/SIGTERM → graceful shutdown hooks, then stop the loop."""

    def _handle_signal(signum, _frame):
        clock.run_shutdown_hooks()
        clock.call_later(0.1, clock.stop_loop)

    if portal_mode:
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
    _install_signal_handlers(portal_mode)

    import evennia

    if not getattr(evennia, "_LOADED", False):
        evennia._init(portal_mode=portal_mode)

    if portal_mode:
        service = evennia.EVENNIA_PORTAL_SERVICE
    else:
        service = evennia.EVENNIA_SERVER_SERVICE

    if "test" not in sys.argv:
        _setup_process_logging(portal_mode, args.nodaemon)

    # Twisted 24+: startService() only sets running=1; twistd called
    # privilegedStartService() first (registers listeners, maintenance, etc.).
    service.privilegedStartService()
    service.startService()

    try:
        loop.run_forever()
    finally:
        try:
            if service.running:
                service.stopService()
        except Exception:
            pass
        _remove_pidfile(args.pidfile)
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass


def run_portal(argv=None):
    run_bootstrap(portal_mode=True, argv=argv)


def run_server(argv=None):
    run_bootstrap(portal_mode=False, argv=argv)
