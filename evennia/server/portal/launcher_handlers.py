"""Transport-agnostic Portal launcher command handlers (T3 S10)."""

from evennia.server.portal import amp
from evennia.utils import logger


def receive_launcher_command(protocol, operation: str, arguments_wire: bytes):
    """Handle a launcher → Portal control operation.

    ``protocol`` is an :class:`~evennia.server.portal.amp_server.AMPServerProtocol`
    instance (provides ``factory``, ``start_server``, ``stop_server``, etc.).
    """
    operation = str(operation)
    _, server_connected, _, _, _, _ = protocol.get_status()

    if operation == amp.SSTART:
        if not server_connected:
            protocol.wait_for_server_connect(protocol.send_Status2Launcher)
            protocol.start_server(amp.loads_launcher_args(arguments_wire))

    elif operation == amp.SRELOAD:
        if server_connected:
            protocol.factory.server_connection.wait_for_disconnect(protocol.send_Status2Launcher)
            protocol.stop_server(mode="reload")
        else:
            protocol.wait_for_server_connect(protocol.send_Status2Launcher)
            protocol.start_server(amp.loads_launcher_args(arguments_wire))

    elif operation == amp.SRESET:
        if server_connected:
            protocol.factory.server_connection.wait_for_disconnect(protocol.send_Status2Launcher)
            protocol.stop_server(mode="reset")
        else:
            protocol.wait_for_server_connect(protocol.send_Status2Launcher)
            protocol.start_server(amp.loads_launcher_args(arguments_wire))

    elif operation == amp.SSHUTD:
        if server_connected:
            protocol.factory.server_connection.wait_for_disconnect(protocol.send_Status2Launcher)
            protocol.stop_server(mode="shutdown")

    elif operation == amp.PSHUTD:
        if server_connected:
            protocol.factory.server_connection.wait_for_disconnect(protocol.factory.portal.shutdown)
        else:
            protocol.factory.portal.shutdown()

    else:
        logger.log_err("Operation {} not recognized".format(operation))
        raise Exception("operation %(op)s not recognized." % {"op": operation})
