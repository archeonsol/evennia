"""Portal-side Portal<->Server IPC handlers (transport-agnostic)."""

from evennia.server import ipc_schema
from evennia.server.portal import amp
from evennia.utils import logger

import evennia


def receive_server2portal(packed_data):
    """Server -> Portal session data plane (trusted output)."""
    try:
        sessid, kwargs = ipc_schema.parse_session(packed_data, enforce_limits=False)
        session = evennia.PORTAL_SESSION_HANDLER.get(sessid, None)
        if session:
            evennia.PORTAL_SESSION_HANDLER.data_out(session, **kwargs)
    except Exception:
        logger.log_trace("packed_data len {}".format(len(packed_data)))
    return {}


def receive_adminserver2portal(link, packed_data):
    """Server -> Portal admin/control plane."""
    link.factory.server_connection = link

    sessid, operation, kwargs = ipc_schema.parse_admin(packed_data)
    portal_sessionhandler = evennia.PORTAL_SESSION_HANDLER

    if operation == amp.SLOGIN:
        session = portal_sessionhandler.get(sessid)
        if session:
            portal_sessionhandler.server_logged_in(session, kwargs.get("sessiondata"))

    elif operation == amp.SDISCONN:
        session = portal_sessionhandler.get(sessid)
        if session:
            portal_sessionhandler.server_disconnect(session, reason=kwargs.get("reason"))

    elif operation == amp.SDISCONNALL:
        portal_sessionhandler.server_disconnect_all(reason=kwargs.get("reason"))

    elif operation == amp.SRELOAD:
        link.factory.server_connection.wait_for_disconnect(
            link.start_server, link.factory.portal.server_twistd_cmd
        )
        link.stop_server(mode="reload")

    elif operation == amp.SRESET:
        link.factory.server_connection.wait_for_disconnect(
            link.start_server, link.factory.portal.server_twistd_cmd
        )
        link.stop_server(mode="reset")

    elif operation == amp.SSHUTD:
        link.stop_server(mode="shutdown")

    elif operation == amp.PSHUTD:
        link.factory.server_connection.wait_for_disconnect(link.factory.portal.shutdown)
        link.stop_server(mode="shutdown")

    elif operation == amp.PSYNC:
        link.factory.portal.server_info_dict = kwargs.get("info_dict", {})
        link.factory.portal.server_process_id = kwargs.get("spid", None)
        server_restart_mode = link.factory.portal.server_restart_mode

        sessdata = evennia.PORTAL_SESSION_HANDLER.get_all_sync_data()
        send_adminportal2server(
            link,
            amp.DUMMYSESSION,
            amp.PSYNC,
            server_restart_mode=server_restart_mode,
            sessiondata=sessdata,
            portal_start_time=link.factory.portal.start_time,
        )
        evennia.PORTAL_SESSION_HANDLER.at_server_connection()
        link.factory.portal.server_restart_mode = None

        if link.factory.server_connection:
            for callback, args, kw in link.factory.server_connect_callbacks:
                try:
                    callback(*args, **kw)
                except Exception:
                    logger.log_trace()
            link.factory.server_connect_callbacks = []

    elif operation == amp.SSYNC:
        portal_sessionhandler.server_session_sync(
            kwargs.get("sessiondata"), kwargs.get("clean", True)
        )
        link.factory.server_restart_mode = "shutdown"

    elif operation == amp.SCONN:
        portal_sessionhandler.server_connect(**kwargs)

    else:
        raise Exception("operation %(op)s not recognized." % {"op": operation})
    return {}


def data_to_server(link, command, sessid, **kwargs):
    """Pack and publish/send a frame to the Server."""
    if command in (amp.AdminPortal2Server,):
        packed = amp.dumps_admin((sessid, kwargs))
    else:
        packed = amp.dumps_session((sessid, kwargs))
    if getattr(link.factory, "server_connection", None):
        conn = link.factory.server_connection
        return conn.callRemote(command, packed_data=packed).addErrback(link.errback, command.key)
    return link.broadcast(command, sessid, packed_data=packed)


def send_msgportal2server(link, session, **kwargs):
    return data_to_server(link, amp.MsgPortal2Server, session.sessid, **kwargs)


def send_adminportal2server(link, session, operation="", **kwargs):
    return data_to_server(link, amp.AdminPortal2Server, session.sessid, operation=operation, **kwargs)
