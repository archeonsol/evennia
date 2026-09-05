"""Server-side Portal<->Server IPC handlers (transport-agnostic)."""

import evennia
from evennia.server import ipc_schema
from evennia.server.portal import amp
from evennia.utils import logger


def receive_msgportal2server(packed_data):
    """Portal -> Server session data plane (untrusted input)."""
    sessid, kwargs = ipc_schema.parse_session(packed_data, enforce_limits=True)
    session = evennia.SERVER_SESSION_HANDLER.get(sessid, None)
    if session:
        evennia.SERVER_SESSION_HANDLER.data_in(session, **kwargs)
    return {}


def receive_adminportal2server(packed_data):
    """Portal -> Server admin/control plane."""
    sessid, operation, kwargs = ipc_schema.parse_admin(packed_data)

    if operation == amp.PCONN:
        link = evennia.EVENNIA_SERVER_SERVICE.portal_bus
        data = kwargs.get("sessiondata")
        if link is not None and "_socket_id" in data:
            link._sessions.connect(data)
            state = link._sessions.server_state()["sessions"]
            link.send_AdminServer2Portal(
                amp.DUMMYSESSION,
                operation=amp.SSYNC,
                sessiondata={sessid: state[sessid]} if sessid in state else {},
                clean=False,
                confirmed=True,
            )
        else:
            evennia.SERVER_SESSION_HANDLER.portal_connect(data)

    elif operation == amp.PCONNSYNC:
        link = evennia.EVENNIA_SERVER_SERVICE.portal_bus
        data = kwargs.get("sessiondata")
        if link is not None and "_socket_id" in data:
            link._sessions.update(data)
        else:
            evennia.SERVER_SESSION_HANDLER.portal_session_sync(data)

    elif operation == amp.PDISCONN:
        session = evennia.SERVER_SESSION_HANDLER.get(sessid)
        if session:
            evennia.SERVER_SESSION_HANDLER.portal_disconnect(session)

    elif operation == amp.PDISCONNALL:
        evennia.SERVER_SESSION_HANDLER.portal_disconnect_all()

    elif operation == amp.PSYNC:
        if evennia.EVENNIA_SERVER_SERVICE.portal_bus is not None:
            return {}
        server_restart_mode = kwargs.get("server_restart_mode", "shutdown")
        evennia.EVENNIA_SERVER_SERVICE.run_init_hooks(server_restart_mode)
        evennia.SERVER_SESSION_HANDLER.portal_sessions_sync(kwargs.get("sessiondata"))
        evennia.SERVER_SESSION_HANDLER.portal_start_time = kwargs.get("portal_start_time")

    elif operation == amp.SRELOAD:
        evennia.SERVER_SESSION_HANDLER.all_sessions_portal_sync()
        evennia.EVENNIA_SERVER_SERVICE.request_shutdown(mode="reload")

    elif operation == amp.SRESET:
        evennia.SERVER_SESSION_HANDLER.all_sessions_portal_sync()
        evennia.EVENNIA_SERVER_SERVICE.request_shutdown(mode="reset")

    elif operation == amp.SSHUTD:
        evennia.EVENNIA_SERVER_SERVICE.request_shutdown(mode="shutdown")

    else:
        raise Exception("operation %(op)s not recognized." % {"op": operation})

    return {}


def data_to_portal(link, command, sessid, **kwargs):
    """Pack and send a frame to the Portal via ``link`` (bus or AMP protocol)."""
    if command in (amp.AdminServer2Portal,):
        packed = amp.dumps_admin((sessid, kwargs))
    else:
        packed = amp.dumps_session((sessid, kwargs))
    return link.callRemote(command, packed_data=packed).addErrback(link.errback, command.key)


def send_msgserver2portal(link, session, **kwargs):
    return data_to_portal(link, amp.MsgServer2Portal, session.sessid, **kwargs)


def send_adminserver2portal(link, session, operation="", **kwargs):
    return data_to_portal(
        link, amp.AdminServer2Portal, session.sessid, operation=operation, **kwargs
    )
