"""Socket incarnation and authority rules for surviving Server sessions."""

from copy import deepcopy

# These are socket facts, not authentication, binding, or command runtime state.
_METADATA = ("protocol_key", "address", "csessid", "suid", "server_data")
_AUTH = ("uid", "uname", "logged_in", "bid")


class SessionReconciler:
    """Retain runtime objects and track uncertain Server disconnects."""

    def __init__(self, handler):
        """Bind the Server handler and its last confirmed socket metadata."""
        self.handler = handler
        self.portal_id = None
        self.records = {}

    @staticmethod
    def connection_data(data):
        """Use initial protocol authentication only for a genuinely new socket."""
        clean = {key: deepcopy(value) for key, value in data.items() if not key.startswith("_")}
        clean.update(uid=None, uname=None, logged_in=False, bid=None)
        clean.update(
            {
                key: value
                for key, value in data.get("_protocol_auth", {}).items()
                if key in ("uid", "uname", "logged_in")
            }
        )
        return clean

    def adopt(self, snapshot, portal_id):
        """Record sockets reconstructed once during process startup."""
        self.portal_id = portal_id
        self.records = {sid: deepcopy(data) for sid, data in snapshot.items()}

    def reconcile(self, snapshot, portal_id):
        """Apply current membership while preserving Server-owned live state."""
        if portal_id != self.portal_id:
            for session in list(self.handler.values()):
                self.handler.portal_disconnect(session)
            self.records.clear()
            self.portal_id = portal_id
        for sid, session in list(self.handler.items()):
            previous = self.records.get(sid)
            current = snapshot.get(sid)
            if current is None or (previous and previous["_socket_id"] != current["_socket_id"]):
                self.handler.portal_disconnect(session)
        for sid, data in snapshot.items():
            previous = self.records.get(sid)
            same_socket = previous and previous["_socket_id"] == data["_socket_id"]
            session = self.handler.get(sid)
            if not same_socket:
                self.handler.portal_connect(self.connection_data(data))
                session = self.handler.get(sid)
            elif session is not None:
                for key in _METADATA:
                    if key in data and data.get(key) != previous.get(key):
                        setattr(session, key, deepcopy(data[key]))
                old_flags = previous.get("protocol_flags", {})
                for key, value in data.get("protocol_flags", {}).items():
                    if key not in old_flags or value != old_flags[key]:
                        session.protocol_flags[key] = deepcopy(value)
            self.records[sid] = deepcopy(data)
        self.records = {sid: data for sid, data in self.records.items() if sid in snapshot}

    def server_state(self):
        """Return auth mirrors and explicit disconnects, scoped to incarnations."""
        sessions = {}
        closed = {}
        for sid, data in self.records.items():
            session = self.handler.get(sid)
            if session is None:
                closed[sid] = data["_socket_id"]
            else:
                sessions[sid] = {
                    "_socket_id": data["_socket_id"],
                    **{key: getattr(session, key, None) for key in _AUTH},
                }
        return {"sessions": sessions, "closed": closed}

    def connect(self, data):
        """Apply a normal ordered PCONN without replacing the whole membership."""
        snapshot = dict(self.records)
        snapshot[data["sessid"]] = data
        self.reconcile(snapshot, self.portal_id)

    def update(self, data):
        """Apply negotiated metadata only to the same live socket incarnation."""
        sid = data["sessid"]
        old = self.records.get(sid)
        if old and old["_socket_id"] == data.get("_socket_id"):
            snapshot = dict(self.records)
            snapshot[sid] = {**old, **data}
            self.reconcile(snapshot, self.portal_id)
