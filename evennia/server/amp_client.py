"""
The Evennia Server service acts as an AMP-client when talking to the
Portal. This module sets up the Client-side communication.

"""

import os

from django.conf import settings
from twisted.internet import protocol

import evennia
from evennia.server import ipc_handlers_server
from evennia.server.portal import amp
from evennia.utils import logger
from evennia.utils.utils import class_from_module


class AMPClientFactory(protocol.ReconnectingClientFactory):
    """
    This factory creates an instance of an AMP client connection. This handles communication from
    the be the Evennia 'Server' service to the 'Portal'. The client will try to auto-reconnect on a
    connection error.

    """

    # Initial reconnect delay in seconds.
    initialDelay = 1
    factor = 1.5
    maxDelay = 1
    noisy = False

    def __init__(self, server):
        """
        Initializes the client factory.

        Args:
            server (server): server instance.

        """
        self.server = server
        self.protocol = class_from_module(settings.AMP_CLIENT_PROTOCOL_CLASS)
        self.maxDelay = 10
        # not really used unless connecting to multiple servers, but
        # avoids having to check for its existence on the protocol
        self.broadcasts = []

    def startedConnecting(self, connector):
        """
        Called when starting to try to connect to the Portal AMP server.

        Args:
            connector (Connector): Twisted Connector instance representing
                this connection.

        """
        pass

    def buildProtocol(self, addr):
        """
        Creates an AMPProtocol instance when connecting to the AMP server.

        Args:
            addr (str): Connection address. Not used.

        """
        self.resetDelay()
        self.server.portal_bus = AMPServerClientProtocol()
        self.server.portal_bus.factory = self
        self.server.amp_protocol = self.server.portal_bus  # deprecated alias
        return self.server.portal_bus

    def clientConnectionLost(self, connector, reason):
        """
        Called when the AMP connection to the MUD server is lost.

        Args:
            connector (Connector): Twisted Connector instance representing
                this connection.
            reason (str): Eventual text describing why connection was lost.

        """
        logger.log_info("Server disconnected from the portal.")
        protocol.ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

    def clientConnectionFailed(self, connector, reason):
        """
        Called when an AMP connection attempt to the MUD server fails.

        Args:
            connector (Connector): Twisted Connector instance representing
                this connection.
            reason (str): Eventual text describing why connection failed.

        """
        logger.log_msg("Attempting to reconnect to Portal ...")
        protocol.ReconnectingClientFactory.clientConnectionFailed(self, connector, reason)


class AMPServerClientProtocol(amp.AMPMultiConnectionProtocol):
    """
    This protocol describes the Server service (acting as an AMP-client)'s communication with the
    Portal (which acts as the AMP-server)

    """

    # sending AMP data

    def connectionMade(self):
        """
        Called when a new connection is established.

        """
        # print("AMPClient new connection {}".format(self))
        info_dict = self.factory.server.get_info_dict()
        super().connectionMade()
        # first thing we do is to request the Portal to sync all sessions
        # back with the Server side. We also need the startup mode (reload, reset, shutdown)
        self.send_AdminServer2Portal(
            amp.DUMMYSESSION, operation=amp.PSYNC, spid=os.getpid(), info_dict=info_dict
        )
        # run the intial setup if needed
        self.factory.server.run_initial_setup()

    def data_to_portal(self, command, sessid, **kwargs):
        return ipc_handlers_server.data_to_portal(self, command, sessid, **kwargs)

    def send_MsgServer2Portal(self, session, **kwargs):
        return ipc_handlers_server.send_msgserver2portal(self, session, **kwargs)

    def send_AdminServer2Portal(self, session, operation="", **kwargs):
        return ipc_handlers_server.send_adminserver2portal(self, session, operation=operation, **kwargs)

    # receiving AMP data

    @amp.MsgStatus.responder
    def server_receive_status(self, question):
        return {"status": "OK"}

    @amp.MsgPortal2Server.responder
    @amp.catch_traceback
    def server_receive_msgportal2server(self, packed_data):
        return ipc_handlers_server.receive_msgportal2server(packed_data)

    @amp.AdminPortal2Server.responder
    @amp.catch_traceback
    def server_receive_adminportal2server(self, packed_data):
        return ipc_handlers_server.receive_adminportal2server(packed_data)
