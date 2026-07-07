import os
import time
from os.path import abspath, dirname

from django.conf import settings
from django.db import connection

import evennia
from evennia.server.service_registry import MultiService
from evennia.utils import clock, logger
from evennia.utils.utils import (class_from_module, get_evennia_version,
                                 make_iter, mod_import)


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
        if self.server_restart_mode:
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
        self._last_server_autorestart = now
        logger.log_warn("Server process %s died; Portal auto-restarting it." % spid)
        self.server_process_id = None
        protocol.start_server(self.server_twistd_cmd)

    def _privileged_start(self):
        self.start_time = time.time()
        self.maintenance_task = clock.looping(60, self.portal_maintenance, now=True)
        self._server_watchdog_task = clock.looping(15, self._maybe_restart_dead_server, now=False)
        clock.register_shutdown_hook(self.shutdown, _reactor_stopping=True, _stop_server=True)

        if settings.AMP_HOST and settings.AMP_PORT and settings.AMP_INTERFACE:
            self.register_amp()

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
                        settings.WEBSOCKET_CLIENT_PORT
                        and settings.WEBSOCKET_CLIENT_INTERFACE
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

        factory = amp_server.AMPServerFactory(self)
        self.amp_factory = factory
        self._launcher_amp_protocol = factory.protocol()
        self._launcher_amp_protocol.factory = factory

        if getattr(settings, "SERVER_PORTAL_BUS", "redis") != "redis":
            logger.log_err(
                "SERVER_PORTAL_BUS=%r is no longer supported; use 'redis'."
                % settings.SERVER_PORTAL_BUS
            )
        self.register_redis_bus()

        if settings.AMP_HOST and settings.AMP_PORT and settings.AMP_INTERFACE:
            if _asyncio_loop() is None:
                logger.log_err(
                    "Launcher IPC requires an asyncio bootstrap loop; "
                    "amp:%s not started." % settings.AMP_PORT
                )
            else:
                self.info_dict["amp"] = "amp: %s (asyncio ipc)" % settings.AMP_PORT
                start_launcher_server(
                    self,
                    factory,
                    self._launcher_amp_protocol,
                    settings.AMP_INTERFACE,
                    settings.AMP_PORT,
                )

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
            if loop is None:
                return

            async def _create():
                try:
                    server = await loop.create_server(
                        protocol_factory, interface, port, ssl=ssl
                    )
                    self._asyncio_servers.append(server)
                except Exception:
                    logger.log_trace("asyncio Portal server failed to start")

            loop.create_task(_create())

        clock.when_running(_go)

    def _start_asyncio_proxy(self, interface, proxyport, upstream_host, upstream_port):
        from evennia.server.portal.web_proxy import ReverseProxy

        proxy = ReverseProxy(upstream_host, upstream_port)
        self._asyncio_proxies.append(proxy)

        def _go():
            loop = _asyncio_loop()
            if loop is not None:
                loop.create_task(proxy.start(interface, proxyport))

        clock.when_running(_go)

    def _start_asyncio_ssh(self, sessionhandler, interface, port):
        from evennia.server.portal.ssh_asyncio import start_ssh_server

        def _go():
            loop = _asyncio_loop()
            if loop is None:
                return

            async def _create():
                try:
                    server = await start_ssh_server(sessionhandler, interface, port)
                    self._asyncio_servers.append(server)
                except Exception:
                    logger.log_trace("asyncio SSH server failed to start")

            loop.create_task(_create())

        clock.when_running(_go)

    def _stop_asyncio_servers(self):
        loop = _asyncio_loop()
        for server in self._asyncio_servers:
            try:
                server.close()
            except Exception:
                pass
        if loop is not None:
            for proxy in self._asyncio_proxies:
                try:
                    loop.create_task(proxy.stop())
                except Exception:
                    pass
        self._asyncio_servers = []
        self._asyncio_proxies = []

    def _get_backup_server_cmd(self):
        import sys

        from evennia.server.asyncio_bootstrap import build_cmdline

        gamedir = os.getcwd()
        pidfile = os.path.join(gamedir, "server", "server.pid") if os.name != "nt" else None
        _, server_cmd = build_cmdline(
            portal_py_file=os.path.join(
                dirname(dirname(abspath(__file__))), "portal", "portal.py"
            ),
            server_py_file=os.path.join(dirname(dirname(abspath(__file__))), "server.py"),
            server_pidfile=pidfile,
        )
        return server_cmd

    def get_info_dict(self):
        return self.info_dict

    def shutdown(self, _reactor_stopping=False, _stop_server=False):
        if _reactor_stopping and hasattr(self, "shutdown_complete"):
            return

        evennia.PORTAL_SESSION_HANDLER.disconnect_all()
        self._stop_asyncio_servers()
        if _stop_server:
            self.server_amp.stop_server(mode="shutdown")
        if not _reactor_stopping:
            self.shutdown_complete = True
            protocol = getattr(self, "_launcher_amp_protocol", None)
            if protocol is not None:
                try:
                    protocol.send_Status2Launcher()
                except Exception:
                    logger.log_trace("portal shutdown status push failed")
            clock.call_later(0, clock.stop_loop)
