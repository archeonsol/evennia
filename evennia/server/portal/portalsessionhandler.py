"""
Sessionhandler for portal sessions.

"""

import time
from collections import deque, namedtuple
from uuid import uuid4

from django.conf import settings
from django.utils.translation import gettext as _

import evennia
from evennia.server.portal.amp import PCONN, PCONNSYNC, PDISCONN, PDISCONNALL
from evennia.server.sessionhandler import SessionHandler
from evennia.utils import clock
from evennia.utils.logger import log_trace
from evennia.utils.utils import class_from_module

# module import
_MOD_IMPORT = None

# global throttles
_MAX_CONNECTION_RATE = float(settings.MAX_CONNECTION_RATE)
# per-session throttles
_MAX_COMMAND_RATE = float(settings.MAX_COMMAND_RATE)
_MAX_CHAR_LIMIT = int(settings.MAX_CHAR_LIMIT)

_MIN_TIME_BETWEEN_CONNECTS = 1.0 / float(_MAX_CONNECTION_RATE)
_MIN_TIME_BETWEEN_COMMANDS = 1.0 / float(_MAX_COMMAND_RATE)

_CONNECTION_QUEUE = deque()

DUMMYSESSION = namedtuple("DummySession", ["sessid"])(0)

# -------------------------------------------------------------
# Portal-SessionHandler class
# -------------------------------------------------------------

DOS_PROTECTION_MSG = _(
    "{servername} DoS protection is active.You are queued to connect in {num} seconds ..."
)


class PortalSessionHandler(SessionHandler):
    """
    This object holds the sessions connected to the portal at any time.
    It is synced with the server's equivalent SessionHandler over the AMP
    connection.

    Sessions register with the handler using the connect() method. This
    will assign a new unique sessionid to the session and send that sessid
    to the server using the AMP connection.

    """

    def __init__(self, *args, **kwargs):
        """
        Init the handler

        """
        super().__init__(*args, **kwargs)
        self.latest_sessid = 0
        self.bus_revision = 0
        self.uptime = time.time()
        self.connection_time = 0

        self.connection_last = self.uptime
        self.connection_task = None

    def _ensure_bus_socket(self, session):
        """Capture socket identity and protocol auth before Server mirrors arrive."""
        if not getattr(session, "_bus_socket_id", None):
            session._bus_socket_id = uuid4().hex
            session._bus_protocol_auth = {
                key: getattr(session, key, None) for key in ("uid", "uname", "logged_in")
            }
            session._bus_confirmed = False

    def get_bus_sync_data(self):
        """Return every live socket, including sockets awaiting connection admission."""
        snapshot = {}
        for sid, session in self.items():
            self._ensure_bus_socket(session)
            snapshot[sid] = session.get_sync_data()
        return snapshot

    def apply_bus_state(self, payload):
        """Apply auth and disconnects without crossing socket incarnations."""
        for sid, data in payload["sessions"].items():
            session = self.get(sid)
            if session is not None and session._bus_socket_id == data["_socket_id"]:
                session.load_sync_data(
                    {key: value for key, value in data.items() if key != "_socket_id"}
                )
                session.at_auth_sync()
                session.server_connected = True
                session._bus_confirmed = True
                if session in _CONNECTION_QUEUE:
                    _CONNECTION_QUEUE.remove(session)
        for sid, socket_id in payload["closed"].items():
            session = self.get(sid)
            if session is not None and session._bus_socket_id == socket_id:
                self.server_disconnect(session)

    def apply_final_bus_state(self, sessions):
        """Save final Server state without overwriting newer socket negotiation."""
        for sid, data in sessions.items():
            session = self.get(sid)
            if session is None or session._bus_socket_id != data.get("_socket_id"):
                continue
            baseline = data.get("_portal_flags", {})
            flags = dict(data.get("protocol_flags", {}))
            flags.update(
                {
                    key: value
                    for key, value in session.protocol_flags.items()
                    if key not in baseline or value != baseline[key]
                }
            )
            clean = {key: value for key, value in data.items() if not key.startswith("_")}
            clean["protocol_flags"] = flags
            session.load_sync_data(clean)
            session._bus_confirmed = True

    def at_server_connection(self):
        """
        Called when the Portal establishes connection with the Server.
        At this point, the AMP connection is already established.

        """
        self.connection_time = time.time()

    def generate_sessid(self):
        """
        Simply generates a sessid that's guaranteed to be unique for this Portal run.

        Returns:
            sessid

        """
        while True:
            self.latest_sessid += 1
            if self.latest_sessid not in self:
                return self.latest_sessid

    def connect(self, session):
        """
        Called by protocol at first connect. This adds a not-yet
        authenticated session using an ever-increasing counter for
        sessid.

        Args:
            session (PortalSession): The Session connecting.

        Notes:
            We implement a throttling mechanism here to limit the speed at
            which new connections are accepted - this is both a stop
            against DoS attacks as well as helps using the Dummyrunner
            tester with a large number of connector dummies.

        """
        global _CONNECTION_QUEUE

        if session:
            # assign if we are first-connectors
            if not session.sessid:
                # if the session already has a sessid (e.g. being inherited in the
                # case of a webclient auto-reconnect), keep it
                session.sessid = self.generate_sessid()
            self._ensure_bus_socket(session)
            self[session.sessid] = session
            self.bus_revision += 1
            session.server_connected = False
            _CONNECTION_QUEUE.appendleft(session)
            if len(_CONNECTION_QUEUE) > 1:
                session.data_out(
                    text=(
                        (
                            DOS_PROTECTION_MSG.format(
                                servername=settings.SERVERNAME,
                                num=len(_CONNECTION_QUEUE) * _MIN_TIME_BETWEEN_CONNECTS,
                            ),
                        ),
                        {},
                    )
                )
        now = time.time()
        if (
            (now - self.connection_last < _MIN_TIME_BETWEEN_CONNECTS)
            or not evennia.EVENNIA_PORTAL_SERVICE.server_amp
            or not getattr(evennia.EVENNIA_PORTAL_SERVICE.server_amp, "ready", True)
        ):
            if not session or not self.connection_task:
                self.connection_task = clock.call_later(
                    _MIN_TIME_BETWEEN_CONNECTS, self.connect, None
                )
            self.connection_last = now
            return
        elif not session:
            if _CONNECTION_QUEUE:
                # keep launching tasks until queue is empty
                self.connection_task = clock.call_later(
                    _MIN_TIME_BETWEEN_CONNECTS, self.connect, None
                )
            else:
                self.connection_task = None
        self.connection_last = now

        if _CONNECTION_QUEUE:
            # sync with server-side
            session = _CONNECTION_QUEUE.pop()
            sessdata = session.get_sync_data()

            self[session.sessid] = session
            result = evennia.EVENNIA_PORTAL_SERVICE.server_amp.send_AdminPortal2Server(
                session, operation=PCONN, sessiondata=sessdata
            )
            session.server_connected = bool(getattr(result, "admitted", True))

    def sync(self, session):
        """
        Called by the protocol of an already connected session. This
        can be used to sync the session info in a delayed manner, such
        as when negotiation and handshakes are delayed.

        Args:
            session (PortalSession): Session to sync.

        """
        self.bus_revision += 1
        if session.sessid and session.server_connected:
            # only use if session already has sessid and has already connected
            # once to the server - if so we must re-sync woth the server, otherwise
            # we skip this step.
            sessdata = session.get_sync_data()
            if evennia.EVENNIA_PORTAL_SERVICE.server_amp and getattr(
                evennia.EVENNIA_PORTAL_SERVICE.server_amp, "ready", True
            ):
                # we only send sessdata that should not have changed
                # at the server level at this point
                sessdata = dict(
                    (key, val)
                    for key, val in sessdata.items()
                    if key
                    in (
                        "protocol_key",
                        "address",
                        "sessid",
                        "csessid",
                        "conn_time",
                        "protocol_flags",
                        "server_data",
                        "_socket_id",
                    )
                )
                evennia.EVENNIA_PORTAL_SERVICE.server_amp.send_AdminPortal2Server(
                    session, operation=PCONNSYNC, sessiondata=sessdata
                )

    def disconnect(self, session):
        """
        Called from portal when the connection is closed from the
        portal side.

        Args:
            session (PortalSession): Session to disconnect.
            delete (bool, optional): Delete the session from
                the handler. Only time to not do this is when
                this is called from a loop, such as from
                self.disconnect_all().

        """
        global _CONNECTION_QUEUE
        self.bus_revision += 1
        if session in _CONNECTION_QUEUE:
            self.pop(session.sessid, None)
            # connection was already dropped before we had time
            # to forward this to the Server, so now we just remove it.
            _CONNECTION_QUEUE.remove(session)
            return

        if session.sessid in self and not hasattr(self, "_disconnect_all"):
            # if this was called directly from the protocol, the
            # connection is already dead and we just need to cleanup
            del self[session.sessid]

        # Tell the Server to disconnect its version of the Session as well.
        # Guard against AMP not yet being established (e.g. very early disconnects).
        if evennia.EVENNIA_PORTAL_SERVICE.server_amp:
            evennia.EVENNIA_PORTAL_SERVICE.server_amp.send_AdminPortal2Server(
                session, operation=PDISCONN
            )

    def disconnect_all(self):
        """
        Disconnect all sessions, informing the Server.

        """
        if settings.TEST_ENVIRONMENT:
            return

        def _callback(result, sessionhandler):
            # we set a watchdog to stop self.disconnect from deleting
            # sessions while we are looping over them.
            sessionhandler._disconnect_all = True
            for session in sessionhandler.values():
                session.disconnect()
            del sessionhandler._disconnect_all

        # inform Server; wait until finished sending before we continue
        # removing all the sessions.

        result = evennia.EVENNIA_PORTAL_SERVICE.server_amp.send_AdminPortal2Server(
            DUMMYSESSION, operation=PDISCONNALL
        )
        result.addCallback(_callback, self)
        result.addErrback(_callback, self)
        return result

    def server_connect(self, protocol_path="", config=dict()):
        """
        Called by server to force the initialization of a new protocol
        instance. Server wants this instance to get a unique sessid and to be
        connected back as normal. This is used to initiate irc/rss etc
        connections.

        Args:
            protocol_path (str): Full python path to the class factory
                for the protocol used, eg
                'evennia.server.portal.irc.IRCClientFactory'
            config (dict): Dictionary of configuration options, fed as
                `**kwarg` to protocol class `__init__` method.

        Raises:
            RuntimeError: If The correct factory class is not found.

        Notes:
            The called protocol class must have a method start()
            that calls the portalsession.connect() as a normal protocol.

        """
        global _MOD_IMPORT
        if not _MOD_IMPORT:
            from evennia.utils.utils import variable_from_module as _MOD_IMPORT
        path, clsname = protocol_path.rsplit(".", 1)
        cls = _MOD_IMPORT(path, clsname)
        if not cls:
            raise RuntimeError("ServerConnect: protocol factory '%s' not found." % protocol_path)
        protocol = cls(self, **config)
        protocol.start()

    def server_disconnect(self, session, reason=""):
        """
        Called by server to force a disconnect by sessid.

        Args:
            session (portalsession): Session to disconnect.
            reason (str, optional): Motivation for disconnect.

        """
        if session:
            session.disconnect(reason)
            if session.sessid in self:
                # in case sess.disconnect doesn't delete it
                del self[session.sessid]
            del session

    def server_disconnect_all(self, reason=""):
        """
        Called by server when forcing a clean disconnect for everyone.

        Args:
            reason (str, optional): Motivation for disconnect.

        """
        for session in list(self.values()):
            session.disconnect(reason)
            del session
        self.clear()

    def server_logged_in(self, session, data):
        """
        The server tells us that the session has been authenticated.
        Update it. Called by the Server.

        Args:
            session (Session): Session logging in.
            data (dict): The session sync data.

        """
        session.load_sync_data(data)
        session.at_login()

    def server_session_sync(self, serversessions, clean=True):
        """
        Server wants to save data to the portal, maybe because it's
        about to shut down. We don't overwrite any sessions here, just
        update them in-place.

        Args:
            serversessions (dict): This is a dictionary

                `{sessid:{property:value},...}` describing
                the properties to sync on all sessions.
            clean (bool): If True, remove any Portal sessions that are
                not included in serversessions.
        """
        to_save = [sessid for sessid in serversessions if sessid in self]
        # save protocols
        for sessid in to_save:
            self[sessid].load_sync_data(serversessions[sessid])
        if clean:
            # disconnect out-of-sync missing protocols
            to_delete = [sessid for sessid in self if sessid not in to_save]
            for sessid in to_delete:
                session = self.get(sessid)
                if session:
                    self.server_disconnect(session)

    def count_loggedin(self, include_unloggedin=False):
        """
        Count loggedin connections, alternatively count all connections.

        Args:
            include_unloggedin (bool): Also count sessions that have
            not yet authenticated.

        Returns:
            count (int): Number of sessions.

        """
        return len(self.get_sessions(include_unloggedin=include_unloggedin))

    def sessions_from_csessid(self, csessid):
        """
        Given a session id, retrieve the session (this is primarily
        intended to be called by web clients)

        Args:
            csessid (int): Session id.

        Returns:
            session (list): The matching session, if found.

        """
        return [
            sess
            for sess in self.get_sessions(include_unloggedin=True)
            if hasattr(sess, "csessid") and sess.csessid and sess.csessid == csessid
        ]

    def announce_all(self, message):
        """
        Send message to all connected sessions.

        Args:
            message (str):  Message to relay.

        Notes:
            This will create an on-the fly text-type
            send command.

        """
        for session in self.values():
            self.data_out(session, text=[[message], {}])

    def bus_unavailable_notice(self, session):
        """Report unavailable or uncertain input locally, at most once per five seconds."""
        now = time.monotonic()
        if now - getattr(session, "_bus_notice_at", -float("inf")) < 5:
            return
        session._bus_notice_at = now
        self.data_out(
            session,
            text=[
                [
                    _(
                        "Connection to the game is unavailable. "
                        "Interrupted actions may have run. Please wait for reconnection."
                    )
                ],
                {},
            ],
        )

    def data_in(self, session, **kwargs):
        """
        Called by portal sessions for relaying data coming
        in from the protocol to the server.

        Args:
            session (PortalSession): Session receiving data.

        Keyword Args:
            kwargs (any): Other data from protocol.

        Notes:
            Data is serialized before passed on.

        """
        try:
            text = kwargs["text"]
            if (_MAX_CHAR_LIMIT > 0) and len(text) > _MAX_CHAR_LIMIT:
                if session:
                    self.data_out(session, text=[[settings.MAX_CHAR_LIMIT_WARNING], {}])
                return
        except Exception:
            # if there is a problem to send, we continue
            pass
        if session:
            now = time.time()

            try:
                command_counter_reset = session.command_counter_reset
            except AttributeError:
                command_counter_reset = session.command_counter_reset = now
                session.command_counter = 0

            # global command-rate limit
            if max(0, now - command_counter_reset) > 1.0:
                # more than a second since resetting the counter. Refresh.
                session.command_counter_reset = now
                session.command_counter = 0

            session.command_counter += 1

            if session.command_counter * _MIN_TIME_BETWEEN_COMMANDS > 1.0:
                self.data_out(session, text=[[settings.COMMAND_RATE_WARNING], {}])
                return

            # Declarative capabilities remain local even before input admission.
            kwargs = self.clean_senddata(session, kwargs)
            from evennia.server.client_negotiation import retain_capabilities

            if retain_capabilities(session, kwargs):
                self.sync(session)
            link = evennia.EVENNIA_PORTAL_SERVICE.server_amp
            if not link or not getattr(link, "ready", True):
                if any(
                    key not in ("azaban_hello", "editor_client", "narrative_client")
                    for key in kwargs
                ):
                    self.bus_unavailable_notice(session)
                return

            # relay data to Server
            result = link.send_MsgPortal2Server(session, **kwargs)
            if not getattr(result, "admitted", True):
                self.bus_unavailable_notice(session)
                return
            session.cmd_last = now

            # eventual local echo (text input only)
            if "text" in kwargs and session.protocol_flags.get("LOCALECHO", False):
                self.data_out(session, text=kwargs["text"])

    def data_out(self, session, **kwargs):
        """
        Called by server for having the portal relay messages and data
        to the correct session protocol.

        Args:
            session (Session): Session sending data.

        Keyword Args:
            kwargs (any): Each key is a command instruction to the
                protocol on the form key = [[args],{kwargs}]. This will
                call a method send_<key> on the protocol. If no such
                method exits, it sends the data to a method send_default.

        """
        # from evennia.server.profiling.timetrace import timetrace  # DEBUG
        # text = timetrace(text, "portalsessionhandler.data_out")  # DEBUG

        # distribute outgoing data to the correct session methods.
        if session:
            for cmdname, (cmdargs, cmdkwargs) in kwargs.items():
                funcname = "send_%s" % cmdname.strip().lower()
                if hasattr(session, funcname):
                    # better to use hassattr here over try..except
                    # - avoids hiding AttributeErrors in the call.
                    try:
                        getattr(session, funcname)(*cmdargs, **cmdkwargs)
                    except Exception:
                        log_trace()
                else:
                    try:
                        # note that send_default always takes cmdname
                        # as arg too.
                        session.send_default(cmdname, *cmdargs, **cmdkwargs)
                    except Exception:
                        log_trace()


# This will be filled in when the portal boots.
PORTAL_SESSIONS = None
