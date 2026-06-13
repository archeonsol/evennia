"""Shared no-DB fakes for default-action provider tests.

These model just enough of the typeclass surface (permissions, locks, access,
msg, search) for the engine to dispatch default actions against plain Python
objects. Provider test fixtures subclass a rules mixin together with
:class:`FakeChar` so the rule methods ride a fake world object.
"""

from types import SimpleNamespace

from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext
from evennia.actions.engine import RuleEngine

__all__ = [
    "FakeAccount",
    "FakeAttributes",
    "FakeChar",
    "FakeLocks",
    "FakeObj",
    "FakePerms",
    "FakeSession",
    "FakeSessionHandler",
    "dispatch",
    "make_actor",
]

ENGINE = RuleEngine()


class FakePerms:
    """Permission handler stub mirroring ``PermissionHandler``'s small surface.

    Stores lowercase strings, as the real handler's ``all()`` returns them.
    """

    def __init__(self, perms=()):
        self._perms = [p.lower() for p in perms]

    def all(self):
        return list(self._perms)

    def add(self, perm):
        perm = perm.lower()
        if perm not in self._perms:
            self._perms.append(perm)

    def remove(self, perm):
        perm = perm.lower()
        self._perms = [p for p in self._perms if p != perm]

    def get(self, perm):
        return perm.lower() in self._perms

    def check(self, *perms, require_all=False):
        hits = [p.lower() in self._perms for p in perms]
        return all(hits) if require_all else any(hits)


class FakeLocks:
    """Lock handler stub: every lockstring check returns a fixed answer."""

    def __init__(self, allow=True):
        self.allow = allow

    def check_lockstring(self, caller, lockstring, **kwargs):
        return self.allow


class FakeAttributes:
    """Attribute handler stub; shares storage with the ``db`` proxy."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None, **kwargs):
        return self._data.get(key, default)

    def add(self, key, value, **kwargs):
        self._data[key] = value

    def remove(self, key, **kwargs):
        self._data.pop(key, None)


class _DbProxy:
    """``obj.db``-style attribute access over a :class:`FakeAttributes` store."""

    def __init__(self, attrs):
        object.__setattr__(self, "_attrs", attrs)

    def __getattr__(self, name):
        return self._attrs._data.get(name)

    def __setattr__(self, name, value):
        self._attrs._data[name] = value


class FakeObj:
    """A world object: key/location/permissions/locks plus message capture."""

    def __init__(
        self,
        key="thing",
        location=None,
        perms=(),
        access=None,
        puppeted=False,
    ):
        self.key = key
        self.name = key
        self.location = location
        self.home = None
        self.permissions = FakePerms(perms)
        self.locks = FakeLocks()
        self.account = None
        self.is_puppeted = puppeted
        self.is_superuser = False
        self.ndb = SimpleNamespace()
        self.attributes = FakeAttributes()
        self.db = _DbProxy(self.attributes)
        self.messages = []
        self.contents_messages = []
        self.executed = []
        self.moves = []
        self.search_map = {}
        self.account_search_map = {}
        self._access = dict(access or {})

    def msg(self, text=None, **kwargs):
        self.messages.append(text)

    def msg_contents(self, text=None, **kwargs):
        self.contents_messages.append(text)

    def access(self, accessing_obj, access_type, default=False):
        return self._access.get(access_type, True)

    def execute_cmd(self, raw_string, **kwargs):
        self.executed.append(raw_string)

    def move_to(self, destination, **kwargs):
        self.moves.append((destination, kwargs))
        self.location = destination
        return True

    def search(self, name, **kwargs):
        return self.search_map.get(name)

    def search_account(self, name, **kwargs):
        return self.account_search_map.get(name)

    def get_display_name(self, looker=None, **kwargs):
        return self.name

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<{type(self).__name__} {self.key}>"


class FakeChar(FakeObj):
    """The acting character."""

    def __init__(self, key="Staff", perms=("Builder",), location=None, **kwargs):
        super().__init__(key=key, location=location, perms=perms, **kwargs)


class FakeAccount(FakeObj):
    """An account: password surface plus character/session bookkeeping."""

    def __init__(self, key="acct", password="secret", valid_password=True, **kwargs):
        super().__init__(key=key, **kwargs)
        self.password = password
        self.valid_password = valid_password
        self.saved = False
        self.character = None

    def check_password(self, password):
        return password == self.password

    def validate_password(self, password):
        if self.valid_password:
            return True, None
        return False, SimpleNamespace(messages=["Password failed validation."])

    def set_password(self, password):
        self.password = password

    def save(self):
        self.saved = True


class FakeSessionHandler:
    """Captures session-handler calls (broadcasts, logins, disconnects)."""

    def __init__(self):
        self.announced = []
        self.logins = []
        self.disconnects = []
        self.synced = []

    def announce_all(self, message):
        self.announced.append(message)

    def login(self, session, account, **kwargs):
        self.logins.append((session, account))

    def disconnect(self, session, reason=""):
        self.disconnects.append((session, reason))

    def session_portal_sync(self, session):
        self.synced.append(session)

    def account_count(self):
        return 1


class FakeSession:
    """A server session: protocol flags plus message/flag-update capture."""

    def __init__(self, flags=None, address="1.2.3.4"):
        self.protocol_flags = dict(
            flags
            if flags is not None
            else {"ANSI": True, "ENCODING": "utf-8", "SCREENREADER": False}
        )
        self.protocol_key = "telnet"
        self.address = address
        self.messages = []
        self.updated_flags = []
        self.sessionhandler = FakeSessionHandler()
        self.ndb = SimpleNamespace()

    def msg(self, text=None, **kwargs):
        self.messages.append(text)

    def update_flags(self, **kwargs):
        self.updated_flags.append(kwargs)


def make_actor(char, session=None, account=None):
    """Build a real :class:`~evennia.actions.actor.Actor` over fakes."""
    return Actor(character=char, session=session, account=account)


def _sync(deferred):
    """Extract an already-fired Deferred's result, re-raising on failure."""
    out = {}
    deferred.addCallbacks(
        lambda r: out.__setitem__("result", r), lambda f: out.__setitem__("fail", f)
    )
    if "fail" in out:
        out["fail"].raiseException()
    if "result" not in out:
        raise AssertionError("dispatch Deferred did not fire synchronously")
    return out["result"]


def dispatch(action, actor, providers, engine=None):
    """Dispatch ``action`` through the engine over an explicit provider list.

    Returns:
        ActionTrace: the dispatch trace (counters live even with
        ``record_phases=False``, matching the production bridge's mode).
    """
    context = ActionContext(providers=list(providers), actor=actor, raw_string="")
    eng = engine or ENGINE
    return _sync(eng.dispatch(action, actor, context, record_phases=False))
