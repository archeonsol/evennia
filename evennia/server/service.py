"""
This module contains the main EvenniaService class, which is the very core of the
Evennia server. It is instantiated by the evennia/server/server.py module.
"""

import importlib
import time
import traceback

import django
import django.db
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.utils import OperationalError
from django.utils.translation import gettext as _

import evennia
from evennia.server.service_registry import MultiService
from evennia.utils import clock, logger
from evennia.utils.utils import get_evennia_version, make_iter, mod_import

_SA = object.__setattr__


class EvenniaServerService(MultiService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.maintenance_count = 0
        self.portal_bus = None  # RedisServerBus (Portal<->Server session IPC)
        self.amp_protocol = None  # deprecated alias of portal_bus
        self.amp_service = None
        self.info_dict = {
            "servername": settings.SERVERNAME,
            "version": get_evennia_version(),
            "amp": "",
            "errors": "",
            "info": "",
            "webserver": "",
            "irc_rss": "",
        }
        self._flush_cache = None
        self._last_server_time_snapshot = 0
        self.maintenance_task = None
        self.stall_watchdog = None
        self.system_driver = None
        self._runtime_config_row = None  # cached ServerConfig row for "runtime"
        self._shutdown_deferred = None
        self._shutdown_task = None
        self._shutdown_request_mode = None
        self._shutdown_in_progress = False

        # Database-specific startup optimizations.
        self.sqlite3_prep()

        self.start_time = 0

        self.start_stop_modules = [
            mod_import(mod)
            for mod in make_iter(settings.AT_SERVER_STARTSTOP_MODULE)
            if isinstance(mod, str)
        ]

    def request_shutdown(self, mode="reload", _reactor_stopping=False):
        """Request the one process-ending Server shutdown task.

        The first request owns the mode. Later signal or IPC requests receive
        the same task instead of entering :meth:`shutdown` concurrently.

        Args:
            mode: One of ``reload``, ``reset``, or ``shutdown``.
            _reactor_stopping: Compatibility marker for process-signal callers.
                The request runner owns loop stop regardless; the domain
                pipeline always runs in its externally coordinated mode.

        Returns:
            The supervised shutdown task.
        """

        if self._shutdown_task is not None:
            return self._shutdown_task
        self._shutdown_request_mode = mode
        self._shutdown_task = clock.create_bound_runtime_task(
            self._run_shutdown_request(mode), task_kind="service"
        )
        return self._shutdown_task

    async def _run_shutdown_request(self, mode):
        """Drain web work and execute one latched domain shutdown."""

        self._shutdown_in_progress = True
        completed = False
        try:
            if hasattr(self, "web_root"):
                await self.web_root.empty_threadpool()
            await self.shutdown(mode, _reactor_stopping=True)
            completed = True
        finally:
            if completed:
                self.shutdown_complete = True
            self._shutdown_in_progress = False
            clock.stop_loop()

    def server_maintenance(self):
        """
        This maintenance function handles repeated checks and updates that
        the server needs to do. It is called every minute.
        """
        if not self._flush_cache:
            from evennia.utils.idmapper.models import conditional_flush as _FLUSH_CACHE

            self._flush_cache = _FLUSH_CACHE

        self.maintenance_count += 1

        now = time.time()
        if self.maintenance_count == 1:
            # first call after a reload
            evennia.gametime.SERVER_START_TIME = now
            evennia.gametime.SERVER_RUNTIME = evennia.ServerConfig.objects.conf(
                "runtime", default=0.0
            )
            # Cache the ServerConfig row so subsequent ticks skip the filter query.
            from evennia.server.models import ServerConfig as _SC

            self._runtime_config_row, _ = _SC.objects.get_or_create(db_key="runtime")
            # self._last_server_time_snapshot is set unconditionally at the end
            # of this method; no separate assignment is needed here.
        else:
            # adjust the runtime not with 60s but with the actual elapsed time
            # in case this may varies slightly from 60s.
            evennia.gametime.SERVER_RUNTIME += now - self._last_server_time_snapshot
        self._last_server_time_snapshot = now

        # update game time and save it across reloads — write directly to the
        # cached row to avoid a filter query on every tick.
        evennia.gametime.SERVER_RUNTIME_LAST_UPDATED = now
        if self._runtime_config_row is not None:
            from evennia.utils.dbserialize import to_pickle

            self._runtime_config_row.db_value = to_pickle(evennia.gametime.SERVER_RUNTIME)
            self._runtime_config_row.save(update_fields=["db_value"])
        else:
            evennia.ServerConfig.objects.conf("runtime", evennia.gametime.SERVER_RUNTIME)

        if self.maintenance_count % 5 == 0:
            # check cache size every 5 minutes
            self._flush_cache(settings.IDMAPPER_CACHE_MAXSIZE)
        if self.maintenance_count % (60 * 7) == 0:
            # drop database connection every 7 hrs to avoid default timeouts on MySQL
            # (see https://github.com/evennia/evennia/issues/1376)
            connection.close()

        # Probe the optional Redis job-queue backend so a real outage stays
        # visible in the log without flooding it (see evennia.jobs.queue).
        try:
            from evennia.jobs.queue import check_redis_backend

            check_redis_backend()
        except Exception:
            logger.log_trace("server_maintenance redis liveness check")

        self.process_idle_timeouts()

        # Link-dead puppets (a body whose session died on a crash) no longer
        # need a tag-scan cleanup: control is anchored in the durable
        # ControlBinding focus stack, and the body is restored on next login by
        # at_post_login / reattach_focus. The old "puppeted" account-tag marker
        # and its periodic at_post_unpuppet sweep are retired.

    def process_idle_timeouts(self):
        # handle idle timeouts
        if settings.IDLE_TIMEOUT > 0:
            now = time.time()
            reason = _("idle timeout exceeded")
            to_disconnect = []
            for session in (
                sess
                for sess in evennia.SESSION_HANDLER.values()
                if (now - sess.cmd_last) > settings.IDLE_TIMEOUT
            ):
                if not session.account or not session.account.access(
                    session.account, "noidletimeout", default=False
                ):
                    to_disconnect.append(session)

            for session in to_disconnect:
                evennia.SESSION_HANDLER.disconnect(session, reason=reason)

    # Server startup methods
    def _privileged_start(self):
        self.start_time = time.time()

        try:
            evennia.ServerConfig.objects.conf("server_starting_mode", True)
        except OperationalError:
            print("Server server_starting_mode couldn't be set - database not set up.")

        self.register_amp()

        if settings.WEBSERVER_ENABLED:
            self.register_webserver()

        try:
            from evennia.server.prometheus_metrics import _init_metrics

            _init_metrics()
        except Exception:
            pass

        ENABLED = []
        if settings.IRC_ENABLED:
            ENABLED.append("irc")
        if settings.RSS_ENABLED:
            ENABLED.append("rss")
        if settings.GRAPEVINE_ENABLED:
            ENABLED.append("grapevine")

        if settings.GAME_INDEX_ENABLED:
            from evennia.server.game_index_client.service import EvenniaGameIndexService

            egi_service = EvenniaGameIndexService()
            egi_service.setServiceParent(self)

        if ENABLED:
            self.info_dict["irc_rss"] = ", ".join(ENABLED) + " enabled."

        self.register_plugins()

        try:
            django.db.connections.close_all()
            evennia.ServerConfig.objects.conf("server_starting_mode", delete=True)
        except OperationalError:
            print("Server server_starting_mode couldn't unset - db not set up.")

    def privilegedStartService(self):
        self._privileged_start()

    def register_plugins(self):
        SERVER_SERVICES_PLUGIN_MODULES = make_iter(settings.SERVER_SERVICES_PLUGIN_MODULES)
        for plugin_module in SERVER_SERVICES_PLUGIN_MODULES:
            # external plugin protocols - load here
            plugin_module = mod_import(plugin_module)
            if plugin_module:
                plugin_module.start_plugin_services(self)
            else:
                print(f"Could not load plugin module {plugin_module}")

    def register_amp(self):
        """Register the Portal<->Server redis bus (session/admin IPC).

        The Portal AMP TCP listener (``register_amp`` on the Portal service) is
        separate and carries launcher control only.
        """
        bus = getattr(settings, "SERVER_PORTAL_BUS", "redis")
        if bus != "redis":
            raise ImproperlyConfigured(
                "SERVER_PORTAL_BUS=%r is not supported; redis is the only "
                "Portal<->Server bus. Set SERVER_PORTAL_BUS='redis' and REDIS_BUS_URL." % bus
            )
        self.register_redis_bus()

    def register_redis_bus(self):
        """Redis Streams bus for Portal<->Server session/admin traffic."""
        from evennia.server.redis_bus import RedisServerBus

        self.info_dict["amp"] = "redis bus"
        self.portal_bus = RedisServerBus(self)
        self.amp_protocol = self.portal_bus  # deprecated alias
        clock.when_running(self.portal_bus.start_bus)

    def register_webserver(self):
        # The Server serves Django over ASGI (uvicorn). The legacy Twisted-WSGI
        # path was retired in the T3 modernization; WEB_SERVER is always "asgi".
        self.register_asgi_webserver()

    def register_asgi_webserver(self):
        """Serve Django over ASGI (uvicorn in a worker thread). See asgi_webserver."""
        from evennia.server.asgi_webserver import UvicornWebService

        self.info_dict["webserver"] = ""
        for _proxyport, serverport in settings.WEBSERVER_PORTS:
            svc = UvicornWebService(serverport, interface="127.0.0.1")
            svc.setName("EvenniaASGIWebServer%s" % serverport)
            svc.setServiceParent(self)
            self.info_dict["webserver"] += "webserver (asgi): %s" % serverport

    def sqlite3_prep(self):
        """
        Optimize some SQLite stuff at startup since we
        can't save it to the database.
        """
        if not (
            hasattr(settings, "DATABASES")
            and settings.DATABASES.get("default", {}).get("ENGINE", None)
            == "django.db.backends.sqlite3"
        ):
            return
        # These pragmas (synchronous/journal_mode) are connection-level tuning
        # that sqlite refuses to change inside a transaction. At server boot the
        # connection is idle so they apply; under a wrapping transaction (e.g. a
        # Django TestCase) the change is both illegal and irrelevant — skip it.
        if connection.in_atomic_block:
            return
        cursor = connection.cursor()
        for pragma in settings.SQLITE3_PRAGMAS:
            cursor.execute(pragma)

    def update_defaults(self):
        """
        We make sure to store the most important object defaults here, so
        we can catch if they change and update them on-objects automatically.
        This allows for changing default cmdset locations and default
        typeclasses in the settings file and have them auto-update all
        already existing objects.

        """

        # setting names
        settings_names = (
            "CMDSET_CHARACTER",
            "CMDSET_ACCOUNT",
            "BASE_ACCOUNT_TYPECLASS",
            "BASE_OBJECT_TYPECLASS",
            "BASE_CHARACTER_TYPECLASS",
            "BASE_ROOM_TYPECLASS",
            "BASE_EXIT_TYPECLASS",
            "BASE_SCRIPT_TYPECLASS",
            "BASE_CHANNEL_TYPECLASS",
        )
        # get previous and current settings so they can be compared
        settings_compare = list(
            zip(
                [evennia.ServerConfig.objects.conf(name) for name in settings_names],
                [settings.__getattr__(name) for name in settings_names],
            )
        )
        mismatches = [
            i for i, tup in enumerate(settings_compare) if tup[0] and tup[1] and tup[0] != tup[1]
        ]
        if len(
            mismatches
        ):  # can't use any() since mismatches may be [0] which reads as False for any()
            # we have a changed default. Import relevant objects and
            # run the update

            # from evennia.accounts.models import AccountDB
            for i, prev, curr in (
                (i, tup[0], tup[1]) for i, tup in enumerate(settings_compare) if i in mismatches
            ):
                # update the database
                self.info_dict["info"] = (
                    " %s:\n '%s' changed to '%s'. Updating unchanged entries in database ..."
                    % (
                        settings_names[i],
                        prev,
                        curr,
                    )
                )
                if i == 0:
                    evennia.ObjectDB.objects.filter(db_cmdset_storage__exact=prev).update(
                        db_cmdset_storage=curr
                    )
                if i == 1:
                    evennia.AccountDB.objects.filter(db_cmdset_storage__exact=prev).update(
                        db_cmdset_storage=curr
                    )
                if i == 2:
                    evennia.AccountDB.objects.filter(db_typeclass_path__exact=prev).update(
                        db_typeclass_path=curr
                    )
                if i in (3, 4, 5, 6):
                    evennia.ObjectDB.objects.filter(db_typeclass_path__exact=prev).update(
                        db_typeclass_path=curr
                    )
                if i == 7:
                    evennia.ScriptDB.objects.filter(db_typeclass_path__exact=prev).update(
                        db_typeclass_path=curr
                    )
                if i == 8:
                    evennia.ChannelDB.objects.filter(db_typeclass_path__exact=prev).update(
                        db_typeclass_path=curr
                    )
                # store the new default and clean caches
                evennia.ServerConfig.objects.conf(settings_names[i], curr)
                evennia.ObjectDB.flush_instance_cache()
                evennia.AccountDB.flush_instance_cache()
                evennia.ScriptDB.flush_instance_cache()
                evennia.ChannelDB.flush_instance_cache()
        # if this is the first start we might not have a "previous"
        # setup saved. Store it now.
        [
            evennia.ServerConfig.objects.conf(settings_names[i], tup[1])
            for i, tup in enumerate(settings_compare)
            if not tup[0]
        ]

    def run_initial_setup(self):
        """
        This is triggered by the amp protocol when the connection
        to the portal has been established.
        This attempts to run the initial_setup script of the server.
        It returns if this is not the first time the server starts.
        Once finished the last_initial_setup_step is set to 'done'

        """

        initial_setup = importlib.import_module(settings.INITIAL_SETUP_MODULE)
        last_initial_setup_step = evennia.ServerConfig.objects.conf("last_initial_setup_step")
        try:
            if not last_initial_setup_step:
                # None is only returned if the config does not exist,
                # i.e. this is an empty DB that needs populating.
                self.info_dict["info"] = " Server started for the first time. Setting defaults."
                initial_setup.handle_setup()
            elif last_initial_setup_step not in ("done", -1):
                # last step crashed, so we weill resume from this step.
                # modules and setup will resume from this step, retrying
                # the last failed module. When all are finished, the step
                # is set to 'done' to show it does not need to be run again.
                self.info_dict["info"] = " Resuming initial setup from step '{last}'.".format(
                    last=last_initial_setup_step
                )
                initial_setup.handle_setup(last_initial_setup_step)
        except Exception:
            # stop server if this happens.
            print(traceback.format_exc())
            if not settings.TEST_ENVIRONMENT or not evennia.SESSION_HANDLER:
                print("Error in initial setup. Stopping Server + Portal.")
                evennia.SESSION_HANDLER.portal_shutdown()

    def create_default_channels(self):
        """
        check so default channels exist on every restart, create if not.

        """

        from evennia import AccountDB, ChannelDB
        from evennia.utils.create import create_channel

        superuser = AccountDB.objects.get(id=1)

        # mudinfo
        mudinfo_chan = settings.CHANNEL_MUDINFO
        if mudinfo_chan and not ChannelDB.objects.filter(db_key__iexact=mudinfo_chan["key"]):
            channel = create_channel(**mudinfo_chan)
            channel.connect(superuser)
        # connectinfo
        connectinfo_chan = settings.CHANNEL_CONNECTINFO
        if connectinfo_chan and not ChannelDB.objects.filter(
            db_key__iexact=connectinfo_chan["key"]
        ):
            channel = create_channel(**connectinfo_chan)
        # default channels
        for chan_info in settings.DEFAULT_CHANNELS:
            if not ChannelDB.objects.filter(db_key__iexact=chan_info["key"]):
                channel = create_channel(**chan_info)
                channel.connect(superuser)

    def run_init_hooks(self, mode):
        """
        Called by the amp client once receiving sync back from Portal

        Args:
            mode (str): One of shutdown, reload or reset

        """
        # warn on hook-registry lint findings. Warn-only by design;
        # strict enforcement lives in the test suite
        # (test_engine_lint_is_clean) so an engine documentation issue
        # cannot block a game from booting.
        if mode != "reload":
            from evennia.hooks.lint import warn_at_startup as _hook_lint

            _hook_lint()

        # start server time and maintenance task. Stop-and-replace so a
        # repeat init (tests) never leaves an orphaned task ticking.
        if self.maintenance_task is not None and self.maintenance_task.running:
            self.maintenance_task.stop()
        self.maintenance_task = clock.looping(60, self.server_maintenance, now=True)  # every minute

        # load declared system modules and start the system-scheduler driver
        # (engine systems first, then settings.SYSTEM_MODULES; a broken
        # declared module is a loud startup failure by design). Stop-and-
        # replace so a repeat init (tests) never leaves two drivers ticking.
        from evennia.utils import systems

        if self.system_driver is not None:
            self.system_driver.stop()
        systems.load_system_modules()
        self.system_driver = systems.SystemDriver()
        self.system_driver.start()

        # update eventual changed defaults
        self.update_defaults()

        # run at_post_load() on cached entities (batched / deferred on reload)
        from evennia.server.at_init_scheduler import run_cached_at_init_burst

        run_cached_at_init_burst(mode)

        self.at_server_init()

        # call correct server hook based on start file value
        if mode == "reload":
            logger.log_msg("Server successfully reloaded.")
            self.at_server_reload_start()
        elif mode == "reset":
            # only run hook, don't purge sessions
            self.at_server_cold_start()
            logger.log_msg("Evennia Server successfully restarted in 'reset' mode.")
        elif mode == "shutdown":
            from evennia.objects.models import ObjectDB

            self.at_server_cold_start()
            # clear eventual lingering session storages
            ObjectDB.objects.clear_all_sessids()
            logger.log_msg("Evennia Server successfully started.")

        # always call this regardless of start type
        self.at_server_start()

        # initialize and start global scripts
        evennia.GLOBAL_SCRIPTS.start()

        # start the reactor-stall watchdog after initial start/migrations finish
        # (no-op if REACTOR_STALL_WARNING_MS is 0). Stop-and-replace so a repeat
        # init (tests) never orphans a watchdog.
        from evennia.utils.reactor_watchdog import ReactorStallWatchdog

        if self.stall_watchdog is not None:
            self.stall_watchdog.stop()
        self.stall_watchdog = ReactorStallWatchdog()
        self.stall_watchdog.start()

    async def _await_hooks(self, instances, hook_name, *args, **kwargs):
        """Run ``hook_name`` on each instance and await any returned awaitables."""
        import asyncio

        tasks = []
        for obj in instances:
            try:
                result = getattr(obj, hook_name)(*args, **kwargs)
                tasks.append(clock.maybe_await(result))
            except Exception:
                logger.log_trace(f"Error invoking {hook_name} on {obj}")
                continue
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self, mode="reload", _reactor_stopping=False):
        """
        Shuts down the server from inside it.

        mode - sets the server restart mode.
           - 'reload' - server restarts, no "persistent" scripts
             are stopped, at_reload hooks called.
           - 'reset' - server restarts, non-persistent scripts stopped,
             at_shutdown hooks called but sessions will not
             be disconnected.
           - 'shutdown' - like reset, but server will not auto-restart.
        _reactor_stopping - this is set if server is stopped by a kill
           command OR this method was already called
           once - in both cases the reactor is
           dead/stopping already.
        """
        if _reactor_stopping and hasattr(self, "shutdown_complete"):
            # this means we have already passed through this method
            # once; we don't need to run the shutdown procedure again.
            return

        # The SIGINT handler pre-sets ``_shutdown_in_progress`` before calling
        # us with ``_reactor_stopping=True``; skip the overlap guard in that
        # case so the first SIGINT-driven shutdown still runs. The guard only
        # applies to fresh SRELOAD/SRESET/SSHUTD entries, which can otherwise
        # race ServerConfig writes and MONITOR/ON_DEMAND saves.
        if not _reactor_stopping:
            if self._shutdown_in_progress:
                return
            self._shutdown_in_progress = True

        if mode == "reload":
            # call restart hooks
            evennia.ServerConfig.objects.conf("server_restart_mode", "reload")
            await self._await_hooks(evennia.ObjectDB.get_all_cached_instances(), "at_server_reload")
            await self._await_hooks(
                evennia.AccountDB.get_all_cached_instances(), "at_server_reload"
            )
            for s in evennia.ScriptDB.get_all_cached_instances():
                if not s.id:
                    continue
                try:
                    await clock.maybe_await(s.at_server_reload)
                except Exception:
                    logger.log_trace(f"Error in at_server_reload on script {s}")
            await clock.maybe_await(evennia.SESSION_HANDLER.all_sessions_portal_sync())
            self.at_server_reload_stop()
            # only save monitor state on reload, not on shutdown/reset
            from evennia.scripts.monitorhandler import MONITOR_HANDLER

            try:
                MONITOR_HANDLER.save()
            except Exception as err:
                logger.log_trace(f"Error saving MonitorHandler state: {err}")
        else:
            if mode == "reset":
                # like shutdown but don't unset the is_connected flag and don't disconnect sessions
                await self._await_hooks(
                    evennia.ObjectDB.get_all_cached_instances(), "at_server_shutdown"
                )
                await self._await_hooks(
                    evennia.AccountDB.get_all_cached_instances(), "at_server_shutdown"
                )
                if self.portal_bus:
                    await clock.maybe_await(evennia.SESSION_HANDLER.all_sessions_portal_sync())
            else:  # shutdown
                accounts = list(evennia.AccountDB.get_all_cached_instances())
                for p in accounts:
                    _SA(p, "is_connected", False)
                await self._await_hooks(
                    evennia.ObjectDB.get_all_cached_instances(), "at_server_shutdown"
                )
                for p in accounts:
                    try:
                        await clock.maybe_await(p.unpuppet_all)
                    except Exception:
                        logger.log_trace(f"Error in unpuppet_all on {p}")
                await self._await_hooks(accounts, "at_server_shutdown")
                evennia.ObjectDB.objects.clear_all_sessids()
            for s in evennia.ScriptDB.get_all_cached_instances():
                if not s.id:
                    continue
                try:
                    await clock.maybe_await(s.at_server_shutdown)
                except Exception:
                    logger.log_trace(f"Error in at_server_shutdown on script {s}")
            evennia.ServerConfig.objects.conf("server_restart_mode", "reset")
            self.at_server_cold_stop()

        # stop the reactor-stall watchdog before teardown
        if self.stall_watchdog is not None:
            self.stall_watchdog.stop()

        # quiesce the system-scheduler driver (stop scheduling AND drain any
        # in-flight fires), then drain the write-behind attribute cache one
        # final time so no dirty rows are lost on exit and no late fire can
        # re-dirty state after the final flush.
        if self.system_driver is not None:
            drain = float(getattr(settings, "SERVER_SHUTDOWN_DRAIN_TIMEOUT", 5.0))
            try:
                await self.system_driver.quiesce(timeout=drain)
            except Exception:
                logger.log_trace("shutdown: system driver quiesce failed")
        try:
            from evennia.typeclasses.attributes import flush_all_dirty

            flush_all_dirty()
        except Exception:
            logger.log_trace("final attribute flush at shutdown")
        try:
            from evennia.typeclasses.jsonb_handler import spool_remaining_dirty

            # Anything the DB refused on the way out, including an exceptional
            # final flush, is diverted to the durable spool rather than lost.
            # It is replayed on the next boot.
            spool_remaining_dirty()
        except Exception:
            logger.log_trace("forced attribute spool at shutdown")

        # on-demand handler state should always be saved.
        from evennia.scripts.ondemandhandler import ON_DEMAND_HANDLER

        try:
            ON_DEMAND_HANDLER.save()
        except Exception as err:
            logger.log_trace(f"Error saving OnDemandHandler state: {err}")

        # always called, also for a reload
        self.at_server_stop()

        if hasattr(self, "web_root"):  # not set very first start
            await self.web_root.empty_threadpool()

        if not _reactor_stopping:
            # kill the server
            self.shutdown_complete = True
            clock.call_later(0, clock.stop_loop)

        # we make sure the proper gametime is saved as late as possible
        evennia.ServerConfig.objects.conf("runtime", evennia.gametime.runtime())

    def get_info_dict(self):
        """
        Return the server info, for display.

        """
        return self.info_dict

    # server start/stop hooks

    def _call_start_stop(self, hookname):
        """
        Helper method for calling hooks on all modules.

        Args:
            hookname (str): Name of hook to call.

        """
        for mod in self.start_stop_modules:
            if hook := getattr(mod, hookname, None):
                hook()

    def at_server_init(self):
        """
        This is called first when the server is starting, before any other hooks, regardless of how it's starting.
        """
        # Replay any attribute documents that a past DB outage diverted to the
        # durable write spool, before normal operation reads them.
        try:
            from evennia.typeclasses.jsonb_handler import reclaim_spooled_writes

            reclaim_spooled_writes()
        except Exception:
            logger.log_trace("at_server_init: spooled-write reclamation failed")
        # Reclaim jobs left in-flight by a crashed worker (expired leases /
        # Redis processing list) so durable jobs are retried, not lost.
        try:
            from evennia.jobs.queue import reclaim_jobs

            reclaim_jobs()
        except Exception:
            logger.log_trace("at_server_init: job reclamation failed")
        self._call_start_stop("at_server_init")

    def at_server_start(self):
        """
        This is called every time the server starts up, regardless of
        how it was shut down.

        """
        self._call_start_stop("at_server_start")

    def at_server_stop(self):
        """
        This is called just before a server is shut down, regardless
        of it is fore a reload, reset or shutdown.

        """
        self._call_start_stop("at_server_stop")

    def at_server_reload_start(self):
        """
        This is called only when server starts back up after a reload.

        """
        self._call_start_stop("at_server_reload_start")

    def at_post_portal_sync(self, mode):
        """
        This is called just after the portal has finished syncing back data to the server
        after reconnecting.

        Args:
            mode (str): One of 'reload', 'reset' or 'shutdown'.

        """

        from evennia.scripts.monitorhandler import MONITOR_HANDLER

        MONITOR_HANDLER.restore(mode == "reload")

        # Un-pause all scripts, stop non-persistent timers
        evennia.ScriptDB.objects.update_scripts_after_server_start()

        # start the task handler
        from evennia.scripts.taskhandler import TASK_HANDLER

        TASK_HANDLER.load()
        TASK_HANDLER.create_delays()

        # start the On-demand handler
        from evennia.scripts.ondemandhandler import ON_DEMAND_HANDLER

        ON_DEMAND_HANDLER.load()

        if mode != "reload":
            self.create_default_channels()

        # delete the temporary setting
        evennia.ServerConfig.objects.conf("server_restart_mode", delete=True)

    def at_server_reload_stop(self):
        """
        This is called only time the server stops before a reload.

        """
        self._call_start_stop("at_server_reload_stop")

    def at_server_cold_start(self):
        """
        This is called only when the server starts "cold", i.e. after a
        shutdown or a reset.

        """
        # Remove non-persistent scripts (they do not survive a cold start)
        from evennia.scripts.models import ScriptDB

        ScriptDB.objects.remove_non_persistent()

        if settings.GUEST_ENABLED:
            for guest in evennia.AccountDB.objects.all().filter(
                db_typeclass_path=settings.BASE_GUEST_TYPECLASS
            ):
                for character in list(guest.characters.all()):
                    if character:
                        character.delete()
                guest.delete()
        self._call_start_stop("at_server_cold_start")

    def at_server_cold_stop(self):
        """
        This is called only when the server goes down due to a shutdown or reset.

        """
        self._call_start_stop("at_server_cold_stop")
