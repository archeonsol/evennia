"""
The Evennia Portal service acts as an AMP-server, handling AMP
communication to the AMP clients connecting to it (by default
these are the Evennia Server and the evennia launcher).

"""

import os
import sys
from subprocess import STDOUT, Popen

from django.conf import settings
from twisted.internet import protocol

import evennia
from evennia.server.portal import amp, ipc_handlers_portal
from evennia.utils import clock, logger
from evennia.utils.utils import class_from_module


def _is_windows():
    return os.name == "nt"


def getenv():
    """
    Get current environment and add PYTHONPATH.

    Returns:
        env (dict): Environment global dict.

    """
    sep = ";" if _is_windows() else ":"
    env = os.environ.copy()
    env["PYTHONPATH"] = sep.join(sys.path)
    return env


def build_status(portal, server_connected):
    """Return the launcher status tuple for one portal.

    Shared by both transports. The only thing they disagree about is how they
    know the server is up -- AMP holds a connection and can ask it, while the
    Redis bus has no socket to the server and reads the process instead -- so
    that answer is the argument and everything after it is computed the same
    way.

    Args:
        portal: The portal service.
        server_connected: Whether the server is currently reachable.

    Returns:
        tuple: portal_live, server_live, portal pid, server pid, and the two
        info dicts.
    """

    return (
        bool(
            (portal.running or getattr(portal, "_launcher_ipc_ready", False))
            and not getattr(portal, "shutdown_complete", False)
        ),
        bool(server_connected),
        os.getpid(),
        portal.server_process_id,
        portal.get_info_dict(),
        portal.server_info_dict,
    )


class AMPServerFactory(protocol.ServerFactory):
    """
    This factory creates AMP Server connection. This acts as the 'Portal'-side communication to the
    'Server' process.

    """

    noisy = False

    def logPrefix(self):
        """
        How this is named in logs

        """
        return "AMP"

    def __init__(self, portal):
        """
        Initialize the factory. This is called as the Portal service starts.

        Args:
            portal (Portal): The Evennia Portal service instance.
            protocol (Protocol): The protocol the factory creates
                instances of.

        """
        self.portal = portal
        self.protocol = class_from_module(settings.AMP_SERVER_PROTOCOL_CLASS)
        self.broadcasts = []
        self.server_connection = None
        self.launcher_connection = None
        self.disconnect_callbacks = {}
        self.server_connect_callbacks = []

    def buildProtocol(self, addr):
        """
        Start a new connection, and store it on the service object.

        Args:
            addr (str): Connection address. Not used.

        Returns:
            protocol (Protocol): The created protocol.

        """
        self.portal.amp_protocol = self.protocol()
        self.portal.amp_protocol.factory = self
        return self.portal.amp_protocol


class AMPServerProtocol(amp.AMPMultiConnectionProtocol):
    """
    Protocol subclass for the AMP-server run by the Portal.

    """

    def connectionLost(self, reason):
        """
        Set up a simple callback mechanism to let the amp-server wait for a connection to close.

        """
        # wipe broadcast and data memory
        super().connectionLost(reason)
        if self.factory.server_connection == self:
            self.factory.server_connection = None
            self.factory.portal.server_info_dict = {}
        if self.factory.launcher_connection == self:
            self.factory.launcher_connection = None

        callback, args, kwargs = self.factory.disconnect_callbacks.pop(self, (None, None, None))
        if callback:
            try:
                callback(*args, **kwargs)
            except Exception:
                logger.log_trace()

    def get_status(self):
        """
        Return status for the Evennia infrastructure.

        Returns:
            status (tuple): The portal/server status and pids
                (portal_live, server_live, portal_PID, server_PID).

        """
        return build_status(
            self.factory.portal,
            bool(
                self.factory.server_connection
                and self.factory.server_connection.transport.connected
            ),
        )

    def data_to_server(self, command, sessid, **kwargs):
        return ipc_handlers_portal.data_to_server(self, command, sessid, **kwargs)

    def start_server(self, server_twistd_cmd):
        """
        (Re-)Launch the Evennia server.

        Args:
            server_twisted_cmd (list): The server start instruction
                to pass to POpen to start the server.

        """
        # start the Server
        print("Portal starting server ... ")
        process = None
        with open(settings.SERVER_LOG_FILE, "a") as logfile:
            # we link stdout to a file in order to catch
            # eventual errors happening before the Server has
            # opened its logger.
            try:
                if _is_windows():
                    # Windows requires special care
                    create_no_window = 0x08000000
                    process = Popen(
                        server_twistd_cmd,
                        env=getenv(),
                        bufsize=-1,
                        stdout=logfile,
                        stderr=STDOUT,
                        creationflags=create_no_window,
                    )

                else:
                    process = Popen(
                        server_twistd_cmd, env=getenv(), bufsize=-1, stdout=logfile, stderr=STDOUT
                    )
            except Exception:
                logger.log_trace()

            self.factory.portal.server_twistd_cmd = server_twistd_cmd
            logfile.flush()
        if process and not _is_windows():
            clock.defer_to_thread(process.wait)
        return

    def wait_for_disconnect(self, callback, *args, **kwargs):
        """
        Add a callback for when this connection is lost.

        Args:
            callback (callable): Will be called with *args, **kwargs
                once this protocol is disconnected.

        """
        self.factory.disconnect_callbacks[self] = (callback, args, kwargs)

    def wait_for_server_connect(self, callback, *args, **kwargs):
        """
        Add a callback for when the Server is sure to have connected.

        Args:
            callback (callable): Will be called with *args, **kwargs
                once the Server handshake with Portal is complete.

        """
        self.factory.server_connect_callbacks.append((callback, args, kwargs))

    def stop_server(self, mode="shutdown"):
        """
        Shut down server in one or more modes.

        Args:
            mode (str): One of 'shutdown', 'reload' or 'reset'.

        """
        if mode == "reload":
            self.send_AdminPortal2Server(
                amp.DUMMYSESSION, operation=amp.SRELOAD, server_restart_mode=mode
            )
        elif mode == "reset":
            self.send_AdminPortal2Server(
                amp.DUMMYSESSION, operation=amp.SRESET, server_restart_mode=mode
            )
        elif mode == "shutdown":
            self.send_AdminPortal2Server(
                amp.DUMMYSESSION, operation=amp.SSHUTD, server_restart_mode=mode
            )
        # store the mode for use once server comes back up again
        self.factory.portal.server_restart_mode = mode

    # sending amp data

    def send_Status2Launcher(self):
        """
        Send a status stanza to the launcher.

        """
        conn = self.factory.launcher_connection
        if conn is None:
            return
        status = self.get_status()
        if hasattr(conn, "push_status"):
            conn.push_status(status)
        elif hasattr(conn, "callRemote"):
            conn.callRemote(amp.MsgStatus, status=amp.dumps_status(status)).addErrback(
                self.errback, amp.MsgStatus.key
            )

    def send_MsgPortal2Server(self, session, **kwargs):
        return ipc_handlers_portal.send_msgportal2server(self, session, **kwargs)

    def send_AdminPortal2Server(self, session, operation="", **kwargs):
        return ipc_handlers_portal.send_adminportal2server(
            self, session, operation=operation, **kwargs
        )

    # receive amp data

    @amp.MsgStatus.responder
    @amp.catch_traceback
    def portal_receive_status(self, status):
        """
        Returns run-status for the server/portal.

        Args:
            status (str): Not used.
        Returns:
            status (dict): The status is a tuple
                (portal_running, server_running, portal_pid, server_pid).

        """
        # print('Received PSTATUS request')
        return {"status": amp.dumps_status(self.get_status())}

    @amp.MsgLauncher2Portal.responder
    @amp.catch_traceback
    def portal_receive_launcher2portal(self, operation, arguments):
        """
        Legacy Twisted AMP entry (dev Windows twistd path only).

        """
        from evennia.server.portal import launcher_handlers

        operation = str(operation, "utf-8")
        self.factory.launcher_connection = self
        launcher_handlers.receive_launcher_command(self, operation, arguments)
        return {}
