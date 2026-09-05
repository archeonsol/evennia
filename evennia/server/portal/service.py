import asyncio
import os
import time
from os.path import abspath, dirname

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

import evennia
from evennia.server.launcher_ipc import stop_launcher_servers
from evennia.server.service_registry import MultiService
from evennia.utils import clock, logger
from evennia.utils.utils import class_from_module, get_evennia_version, make_iter, mod_import


def _asyncio_loop():
    """Return the process-owned asyncio loop, or None before bootstrap runs."""
    return clock.get_bound_loop()


class EvenniaPortalService(MultiService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.amp_protocol = None
        self.server_bus = None
        self.server_process_id = None
        self.server_restart_mode = "shutdown"
        self.server_info_dict = dict()
        self.plugins = list()

        self.start_time = 0
        self._maintenance_count = 0
        self.maintenance_task = None
        self._server_watchdog_task = None
        self._last_server_autorestart = 0.0
        self._asyncio_servers = []
        self._asyncio_proxies = []
        self._asyncio_start_tasks = set()
        self._accepting_asyncio_starts = True
        self._shutdown_task = None
        self._shutdown_stop_server = False
        self._shutdown_publish_status = False
        self._shutdown_stop_loop = False

        self.info_dict = {
            "servername": settings.SERVERNAME,
            "version": get_evennia_version(),
            "errors": "",
            "info": "",
            "lockdown_mode": "",
            "amp": "",
            "telnet": [],
            "telnet_ssl": [],
            "ssh": [],
            "webclient": [],
            "webserver_proxy": [],
            "webserver_internal": [],
        }

        self.server_twistd_cmd = self._get_backup_server_cmd()

    @property
    def server_amp(self):
        """Session/admin link to the Server (redis bus)."""
        return self.server_bus

    def portal_maintenance(self):
        self._maintenance_count += 1

        if self._maintenance_count % (60 * 7) == 0:
            connection.close()

    def _maybe_restart_dead_server(self):
        """Restart the Server if it died while the Portal is still up."""
        from evennia.server.redis_bus import _pid_alive

        if getattr(self, "shutdown_complete", False):
            return
        if self.server_restart_mode or getattr(self, "_lifecycle_unconfirmed", False):
            return
        spid = self.server_process_id
        if not spid or _pid_alive(spid):
            return
        if not self.server_twistd_cmd:
            return
        now = time.monotonic()
        if now - self._last_server_autorestart < 30:
            return
        protocol = getattr(self, "_launcher_amp_protocol", None)
        if not protocol:
            return
        # Stamp the throttle before launching: it is the sole re-fire guard.
        # Do NOT clear server_process_id here. start_server swallows a failed
        # Popen, so a relaunch that never comes back would leave the pid None
        # and the `if not spid` guard above would disarm the watchdog forever.
        # Keeping the (dead) pid lets a later tick, past the throttle, retry.
        self._last_server_autorestart = now
        logger.log_warn("Server process %s died; Portal auto-restarting it." % spid)
        protocol.start_server(self.server_twistd_cmd)

    def _privileged_start(self):
        self.start_time = time.time()

        # Launcher IPC must bind before telnet/web/plugins so cold-start can
        # receive SSTART while the rest of the Portal still initializes.
        if settings.AMP_HOST and settings.AMP_PORT and settings.AMP_INTERFACE:
            self.register_amp()

        self.maintenance_task = clock.looping(60, self.portal_maintenance, now=True)
        self._server_watchdog_task = clock.looping(15, self._maybe_restart_dead_server, now=False)
        clock.register_shutdown_hook(self.shutdown, _reactor_stopping=True, _stop_server=True)

        if settings.TELNET_ENABLED and settings.TELNET_PORTS and settings.TELNET_INTERFACES:
            self.register_telnet()

        if settings.SSL_ENABLED and settings.SSL_PORTS and settings.SSL_INTERFACES:
            self.register_ssl()

        if settings.SSH_ENABLED and settings.SSH_PORTS and settings.SSH_INTERFACES:
            self.register_ssh()

        if settings.WEBSERVER_ENABLED:
            self.register_webserver()

        if settings.LOCKDOWN_MODE:
            self.info_dict["lockdown_mode"] = "  LOCKDOWN_MODE active: Only local connections."

        self.register_plugins()

    def register_plugins(self):
        self.plugins.extend(
            mod_import(module) for module in make_iter(settings.PORTAL_SERVICES_PLUGIN_MODULES)
        )
        for plugin_module in self.plugins:
            if plugin_module:
                plugin_module.start_plugin_services(self)

    def check_lockdown(self, interfaces: list[str]):
        if settings.LOCKDOWN_MODE:
            return ["127.0.0.1"]
        return interfaces

    def register_ssl(self):
        from evennia.server.portal import telnet, telnet_ssl

        _ssl_protocol = class_from_module(settings.SSL_PROTOCOL_CLASS)
        interfaces = self.check_lockdown(settings.SSL_INTERFACES)

        for interface in interfaces:
            ifacestr = ""
            if interface not in ("0.0.0.0", "::") or len(interfaces) > 1:
                ifacestr = "-%s" % interface
            for port in settings.SSL_PORTS:
                ssl_context = telnet_ssl.get_asyncio_ssl_context()
                if ssl_context and self._require_asyncio_loop("telnet+ssl%s" % ifacestr, port):
                    factory = telnet.TelnetServerFactory()
                    factory.noisy = False
                    factory.protocol = _ssl_protocol
                    factory.sessionhandler = evennia.PORTAL_SESSION_HANDLER
                    self._start_asyncio_server(
                        lambda f=factory: telnet.AsyncioTelnetProtocol(f),
                        interface,
                        port,
                        ssl=ssl_context,
                    )
                    self.info_dict["telnet_ssl"].append(
                        "telnet+ssl%s: %s (asyncio)" % (ifacestr, port)
                    )
                else:
                    self.info_dict["telnet_ssl"].append(
                        "telnet+ssl%s: %s (deactivated - keys/cert unset or no loop)"
                        % (ifacestr, port)
                    )

    def register_ssh(self):
        interfaces = self.check_lockdown(settings.SSH_INTERFACES)

        for interface in interfaces:
            ifacestr = ""
            if interface not in ("0.0.0.0", "::") or len(interfaces) > 1:
                ifacestr = "-%s" % interface
            for port in settings.SSH_PORTS:
                if self._require_asyncio_loop("ssh%s" % ifacestr, port):
                    self._start_asyncio_ssh(evennia.PORTAL_SESSION_HANDLER, interface, port)
                    self.info_dict["ssh"].append("ssh%s: %s (asyncio)" % (ifacestr, port))

    def register_webserver(self):
        from evennia.server.webserver import EvenniaReverseProxyResource

        interfaces = self.check_lockdown(settings.WEBSERVER_INTERFACES)
        websocket_started = False
        _websocket_protocol = class_from_module(settings.WEBSOCKET_PROTOCOL_CLASS)
        for interface in interfaces:
            ifacestr = ""
            if interface not in ("0.0.0.0", "::") or len(interfaces) > 1:
                ifacestr = "-%s" % interface

            for proxyport, serverport in settings.WEBSERVER_PORTS:
                web_root = EvenniaReverseProxyResource("127.0.0.1", serverport, "")
                webclientstr = ""
                if settings.WEBCLIENT_ENABLED and settings.WEBSOCKET_CLIENT_ENABLED:
                    if (
                        settings.WEBSOCKET_CLIENT_PORT and settings.WEBSOCKET_CLIENT_INTERFACE
                    ) and not websocket_started:
                        from evennia.server.portal import webclient  # noqa
                        from evennia.server.portal.ws_protocol import WSServerFactory

                        w_interface = (
                            "127.0.0.1"
                            if settings.LOCKDOWN_MODE
                            else settings.WEBSOCKET_CLIENT_INTERFACE
                        )
                        w_ifacestr = ""
                        if (
                            w_interface not in ("0.0.0.0", "::")
                            or len(settings.WEBSERVER_INTERFACES) > 1
                        ):
                            w_ifacestr = "-%s" % w_interface
                        port = settings.WEBSOCKET_CLIENT_PORT

                        factory = WSServerFactory()
                        factory.protocol = _websocket_protocol
                        factory.sessionhandler = evennia.PORTAL_SESSION_HANDLER

                        if self._require_asyncio_loop("webclient-websocket%s" % w_ifacestr, port):
                            from evennia.server.portal.webclient import AsyncioWebSocketProtocol

                            self._start_asyncio_server(
                                lambda f=factory: AsyncioWebSocketProtocol(f), w_interface, port
                            )
                            webclientstr = "webclient-websocket%s: %s (asyncio)" % (
                                w_ifacestr,
                                port,
                            )
                        websocket_started = True
                    if webclientstr:
                        self.info_dict["webclient"].append(webclientstr)

                try:
                    WEB_PLUGINS_MODULE = mod_import(settings.WEB_PLUGINS_MODULE)
                except ImportError:
                    WEB_PLUGINS_MODULE = None
                    self.info_dict["errors"] = (
                        "WARNING: settings.WEB_PLUGINS_MODULE not found - "
                        "copy 'evennia/game_template/server/conf/web_plugins.py to "
                        "mygame/server/conf."
                    )

                if WEB_PLUGINS_MODULE:
                    try:
                        web_root = WEB_PLUGINS_MODULE.at_webproxy_root_creation(web_root)
                    except Exception:
                        self.info_dict["errors"] = (
                            "WARNING: WEB_PLUGINS_MODULE is enabled but at_webproxy_root_creation() "
                            "not found copy 'evennia/game_template/server/conf/web_plugins.py to "
                            "mygame/server/conf."
                        )
                if self._require_asyncio_loop("webserver-proxy%s" % ifacestr, proxyport):
                    self._start_asyncio_proxy(interface, proxyport, "127.0.0.1", serverport)
                    self.info_dict["webserver_proxy"].append(
                        "webserver-proxy%s: %s (asyncio)" % (ifacestr, proxyport)
                    )
                self.info_dict["webserver_internal"].append("webserver: %s" % serverport)

    def register_telnet(self):
        from evennia.server.portal import telnet

        _telnet_protocol = class_from_module(settings.TELNET_PROTOCOL_CLASS)
        interfaces = self.check_lockdown(settings.TELNET_INTERFACES)

        for interface in interfaces:
            ifacestr = ""
            if interface not in ("0.0.0.0", "::") or len(interfaces) > 1:
                ifacestr = "-%s" % interface
            for port in settings.TELNET_PORTS:
                factory = telnet.TelnetServerFactory()
                factory.noisy = False
                factory.protocol = _telnet_protocol
                factory.sessionhandler = evennia.PORTAL_SESSION_HANDLER
                if self._require_asyncio_loop("telnet%s" % ifacestr, port):
                    self._start_asyncio_server(
                        lambda f=factory: telnet.AsyncioTelnetProtocol(f), interface, port
                    )
                    self.info_dict["telnet"].append("telnet%s: %s (asyncio)" % (ifacestr, port))

    def register_amp(self):
        from evennia.server.launcher_ipc import start_launcher_server
        from evennia.server.portal import amp_server

        bus = getattr(settings, "SERVER_PORTAL_BUS", "redis")
        if bus != "redis":
            raise ImproperlyConfigured(
                "SERVER_PORTAL_BUS=%r is not supported; redis is the only "
                "Portal<->Server bus. Set SERVER_PORTAL_BUS='redis'." % bus
            )

        factory = amp_server.AMPServerFactory(self)
        self.amp_factory = factory
        self._launcher_amp_protocol = factory.protocol()
        self._launcher_amp_protocol.factory = factory

        self.register_redis_bus()

        if settings.AMP_HOST and settings.AMP_PORT and settings.AMP_INTERFACE:
            if _asyncio_loop() is None:
                logger.log_err(
                    "Launcher IPC requires an asyncio bootstrap loop; "
                    "amp:%s not started." % settings.AMP_PORT
                )
            else:
                self.info_dict["amp"] = "amp: %s (asyncio ipc)" % settings.AMP_PORT
                task = start_launcher_server(
                    self,
                    factory,
                    self._launcher_amp_protocol,
                    settings.AMP_INTERFACE,
                    settings.AMP_PORT,
                )
                if task is not None:
                    self._asyncio_start_tasks.add(task)
                    task.add_done_callback(self._asyncio_start_tasks.discard)

    def register_redis_bus(self):
        from evennia.server.redis_bus import RedisPortalBus

        if self.info_dict["amp"]:
            self.info_dict["amp"] += " + redis bus"
        else:
            self.info_dict["amp"] = "redis bus"
        self.server_bus = RedisPortalBus(self, factory=self.amp_factory)
        clock.when_running(self.server_bus.start_bus)

    def _require_asyncio_loop(self, label, port):
        if _asyncio_loop() is not None:
            return True
        logger.log_err(
            "PORTAL_ASYNCIO_SERVERS requires an asyncio bootstrap loop; "
            "%s:%s not started." % (label, port)
        )
        return False

    def _start_asyncio_server(self, protocol_factory, interface, port, ssl=None):
        def _go():
            loop = _asyncio_loop()
            if loop is None or not self._accepting_asyncio_starts:
                return

            async def _create():
                try:
                    server = await self._settle_asyncio_start(
                        loop.create_server(protocol_factory, interface, port, ssl=ssl),
                        self._close_asyncio_listener,
                    )
                    if server is not None:
                        self._asyncio_servers.append(server)
                except Exception:
                    logger.log_trace("asyncio Portal server failed to start")

            self._track_asyncio_start(_create(), "evennia-portal-listener-start")

        clock.when_running(_go)

    def _start_asyncio_proxy(self, interface, proxyport, upstream_host, upstream_port):
        from evennia.server.portal.web_proxy import ReverseProxy

        proxy = ReverseProxy(upstream_host, upstream_port)
        self._asyncio_proxies.append(proxy)

        def _go():
            loop = _asyncio_loop()
            if loop is not None and self._accepting_asyncio_starts:

                async def _start():
                    async def _stop_proxy(_server):
                        await proxy.stop()

                    await self._settle_asyncio_start(
                        proxy.start(interface, proxyport),
                        _stop_proxy,
                    )

                self._track_asyncio_start(_start(), "evennia-portal-proxy-start")

        clock.when_running(_go)

    def _start_asyncio_ssh(self, sessionhandler, interface, port):
        from evennia.server.portal.ssh_asyncio import start_ssh_server

        def _go():
            loop = _asyncio_loop()
            if loop is None or not self._accepting_asyncio_starts:
                return

            async def _create():
                try:
                    server = await self._settle_asyncio_start(
                        start_ssh_server(sessionhandler, interface, port),
                        self._close_asyncio_listener,
                    )
                    if server is not None:
                        self._asyncio_servers.append(server)
                except Exception:
                    logger.log_trace("asyncio SSH server failed to start")

            self._track_asyncio_start(_create(), "evennia-portal-ssh-start")

        clock.when_running(_go)

    def _track_asyncio_start(self, coro, name):
        """Retain one supervised Portal startup task until it settles."""

        task = clock.create_bound_runtime_task(coro, task_kind="service")
        task.set_name(name)
        self._asyncio_start_tasks.add(task)
        task.add_done_callback(self._asyncio_start_tasks.discard)
        return task

    @staticmethod
    async def _close_asyncio_listener(server):
        """Close and settle one listener returned by a completed bind."""

        server.close()
        await server.wait_closed()

    async def _settle_asyncio_start(self, awaitable, cleanup):
        """Publish a completed start or clean it before cancellation settles."""

        start_task = asyncio.ensure_future(awaitable)
        try:
            resource = await asyncio.shield(start_task)
        except asyncio.CancelledError as cancelled:
            try:
                resource = await start_task
            except BaseException:
                raise cancelled
            try:
                await cleanup(resource)
            except Exception:
                logger.log_trace("asyncio Portal startup cleanup failed")
            raise cancelled

        if not self._accepting_asyncio_starts:
            try:
                await cleanup(resource)
            except Exception:
                logger.log_trace("asyncio Portal startup cleanup failed")
            return None
        return resource

    async def _stop_asyncio_resources(self):
        """Cancel pending starts and await every Portal-owned listener."""

        self._accepting_asyncio_starts = False
        starts = list(self._asyncio_start_tasks)
        for task in starts:
            if not task.done():
                task.cancel()
        if starts:
            await asyncio.gather(*starts, return_exceptions=True)
        self._asyncio_start_tasks.difference_update(starts)

        servers = list(self._asyncio_servers)
        proxies = list(self._asyncio_proxies)
        waits = []
        for server in servers:
            try:
                server.close()
                waits.append(server.wait_closed())
            except Exception:
                logger.log_trace("asyncio Portal listener close failed")
        waits.append(stop_launcher_servers())
        waits.extend(proxy.stop() for proxy in proxies)
        results = await asyncio.gather(*waits, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.log_err(f"Portal asyncio cleanup failed: {result}")
        self._asyncio_servers = []
        self._asyncio_proxies = []

    def _get_backup_server_cmd(self):
        import sys

        from evennia.server.asyncio_bootstrap import build_cmdline

        gamedir = os.getcwd()
        pidfile = os.path.join(gamedir, "server", "server.pid") if os.name != "nt" else None
        _, server_cmd = build_cmdline(
            portal_py_file=os.path.join(dirname(dirname(abspath(__file__))), "portal", "portal.py"),
            server_py_file=os.path.join(dirname(dirname(abspath(__file__))), "server.py"),
            server_pidfile=pidfile,
        )
        return server_cmd

    def get_info_dict(self):
        return self.info_dict

    def shutdown(self, _reactor_stopping=False, _stop_server=False, _stop_loop=None):
        """Request the one Portal shutdown task and merge monotonic intent.

        Args:
            _reactor_stopping: True when terminal bootstrap cleanup requested
                the shutdown and should not publish launcher status.
            _stop_server: Promote the request to stop the Server process.
            _stop_loop: Explicit normal-completion loop-stop intent. Defaults
                to the inverse of ``_reactor_stopping`` for callback parity.

        Returns:
            The supervised Portal shutdown task.
        """

        self._shutdown_stop_server |= bool(_stop_server)
        self._shutdown_publish_status |= not _reactor_stopping
        if _stop_loop is None:
            _stop_loop = not _reactor_stopping
        self._shutdown_stop_loop |= bool(_stop_loop)
        if self._shutdown_task is None:
            self._shutdown_task = clock.create_bound_runtime_task(
                self._run_shutdown_request(), task_kind="service"
            )
        return self._shutdown_task

    async def _run_shutdown_request(self):
        """Disconnect sessions, settle resources, and honor latched intent."""

        try:
            try:
                import asyncio

                async with asyncio.timeout(5):
                    await clock.maybe_await(evennia.PORTAL_SESSION_HANDLER.disconnect_all())
            except Exception:
                logger.log_trace("portal session disconnect failed")
            await self._stop_asyncio_resources()
            if self._shutdown_stop_server and self.server_amp is not None:
                try:
                    async with asyncio.timeout(5):
                        await clock.maybe_await(self.server_amp.stop_server(mode="shutdown"))
                except Exception:
                    logger.log_trace("portal Server stop request failed")
            if self._shutdown_publish_status:
                self.shutdown_complete = True
                protocol = getattr(self, "_launcher_amp_protocol", None)
                if protocol is not None:
                    try:
                        protocol.send_Status2Launcher()
                    except Exception:
                        logger.log_trace("portal shutdown status push failed")
        finally:
            if self.server_bus is not None:
                self.server_bus.stop_bus()
            if self._shutdown_stop_loop:
                clock.stop_loop()
