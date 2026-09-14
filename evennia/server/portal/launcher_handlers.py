"""Transport-agnostic Portal launcher command handlers (T3 S10)."""

from evennia.server.portal import amp
from evennia.utils import logger


def _server_link(protocol):
    """Return the link that publishes Portal -> Server admin frames.

    The launcher IPC hands these handlers the Portal's ``_launcher_amp_protocol``,
    an :class:`~evennia.server.portal.amp_server.AMPServerProtocol` that was
    never connected to a Server. Its ``broadcast`` fans out over
    ``factory.broadcasts``, which stays empty because redis is the only
    Portal<->Server bus, so publishing through it silently reports success
    while sending nothing. Lifecycle operations therefore resolve the Portal's
    actual bus, the same link ``EvenniaPortalService.server_amp`` exposes.

    Args:
        protocol (AMPServerProtocol): The launcher-facing protocol.

    Returns:
        object: The Portal's Server bus, or ``protocol`` when none is bound.

    """
    return getattr(protocol.factory.portal, "server_bus", None) or protocol


def receive_launcher_command(protocol, operation: str, arguments_wire: bytes):
    """Handle a launcher → Portal control operation.

    ``protocol`` is an :class:`~evennia.server.portal.amp_server.AMPServerProtocol`
    instance (provides ``factory``, ``start_server``, ``stop_server``, etc.).

    Publication goes to the Server bus (see :func:`_server_link`); local
    bookkeeping -- spawning the Server process, launcher status pushes and the
    factory's connect callbacks -- stays on ``protocol``, which shares its
    factory with the bus.
    """
    operation = str(operation)
    _, server_connected, _, _, _, _ = protocol.get_status()
    link = _server_link(protocol)

    if operation == amp.SSTART:
        if not server_connected:
            protocol.wait_for_server_connect(protocol.send_Status2Launcher)
            protocol.start_server(amp.loads_launcher_args(arguments_wire))

    elif operation == amp.SRELOAD:
        if server_connected:
            result = link.stop_server(mode="reload")
            result.addCallback(
                lambda _value: protocol.factory.server_connection.wait_for_disconnect(
                    protocol.send_Status2Launcher
                )
            )
        else:
            protocol.wait_for_server_connect(protocol.send_Status2Launcher)
            protocol.start_server(amp.loads_launcher_args(arguments_wire))

    elif operation == amp.SRESET:
        if server_connected:
            result = link.stop_server(mode="reset")
            result.addCallback(
                lambda _value: protocol.factory.server_connection.wait_for_disconnect(
                    protocol.send_Status2Launcher
                )
            )
        else:
            protocol.wait_for_server_connect(protocol.send_Status2Launcher)
            protocol.start_server(amp.loads_launcher_args(arguments_wire))

    elif operation == amp.SSHUTD:
        if server_connected:
            result = link.stop_server(mode="shutdown")
            result.addCallback(
                lambda _value: protocol.factory.server_connection.wait_for_disconnect(
                    protocol.send_Status2Launcher
                )
            )

    elif operation == amp.PSHUTD:
        if server_connected:
            result = link.stop_server(mode="shutdown")
            result.addCallback(
                lambda _value: protocol.factory.server_connection.wait_for_disconnect(
                    protocol.factory.portal.shutdown
                )
            )
        else:
            protocol.factory.portal.shutdown()

    else:
        logger.log_err("Operation {} not recognized".format(operation))
        raise Exception("operation %(op)s not recognized." % {"op": operation})
