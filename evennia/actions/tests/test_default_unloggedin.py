"""Tests for the engine-shipped unlogged-in actions (``evennia.actions.default.unloggedin``).

The action-engine analogue of the ``CmdUnconnectedConnect``/``Create``/
``Info``/``Encoding``/``Screenreader``/``Help`` command tests: the provider is
the *session* (the unlogged actor's effective object), authentication classes
are faked through the module's ``class_from_module`` seam, and the ``create``
confirmation drives the engine's generator suspension.
"""

import sys
import unittest
from unittest import mock

from django.conf import settings
from twisted.internet.defer import Deferred

from evennia.actions.default import unloggedin as unloggedin_module
from evennia.actions.default.unloggedin import (
    Connect,
    Create,
    Encoding,
    Help,
    Info,
    Look,
    Quit,
    Screenreader,
    SessionLoginRules,
)
from evennia.actions.tests.fakes import FakeSession, dispatch, make_actor

engine_mod = sys.modules["evennia.actions.engine"]


class LoginSession(SessionLoginRules, FakeSession):
    """Provider fixture: an unlogged session carrying the login rules."""


class _FakeAccountCls:
    """Stand-in account typeclass: records authenticate/create calls."""

    auth_calls = []
    create_calls = []
    auth_result = (None, ["Bad credentials."])
    create_result = (None, ["Could not create."])
    normalized = None

    @classmethod
    def reset(cls):
        cls.auth_calls = []
        cls.create_calls = []
        cls.auth_result = (None, ["Bad credentials."])
        cls.create_result = (None, ["Could not create."])
        cls.normalized = None

    @classmethod
    def authenticate(cls, username=None, password=None, ip=None, session=None):
        cls.auth_calls.append({"username": username, "password": password})
        return cls.auth_result

    @classmethod
    def normalize_username(cls, name):
        return cls.normalized if cls.normalized is not None else name

    @classmethod
    def create(cls, username=None, password=None, ip=None, session=None):
        cls.create_calls.append({"username": username, "password": password})
        return cls.create_result


class _FakeGuestCls:
    """Stand-in guest typeclass."""

    auth_result = (None, ["No guests available."])

    @classmethod
    def authenticate(cls, ip=None):
        return cls.auth_result


def _fake_class_from_module(path):
    return _FakeGuestCls if "guest" in path.lower() else _FakeAccountCls


def _setup():
    session = LoginSession()
    actor = make_actor(None, session=session)
    return session, actor


def _patched_dispatch(session, actor, action):
    with mock.patch.object(
        unloggedin_module, "class_from_module", side_effect=_fake_class_from_module
    ):
        return dispatch(action, actor, [session])


# --- connect ---------------------------------------------------------------------
class TestConnect(unittest.TestCase):
    def setUp(self):
        _FakeAccountCls.reset()

    def _connect(self, session, actor, raw):
        action = Connect.parse(raw, actor, verb="connect")
        return _patched_dispatch(session, actor, action)

    def test_successful_login(self):
        session, actor = _setup()
        account = object()
        _FakeAccountCls.auth_result = (account, None)
        self._connect(session, actor, "bob hunter2")
        self.assertEqual(_FakeAccountCls.auth_calls, [{"username": "bob", "password": "hunter2"}])
        self.assertEqual(session.sessionhandler.logins, [(session, account)])

    def test_failed_login_reports_errors(self):
        session, actor = _setup()
        self._connect(session, actor, "bob wrong")
        self.assertTrue(any("Bad credentials" in m for m in session.messages))
        self.assertEqual(session.sessionhandler.logins, [])

    def test_quoted_names_with_spaces(self):
        session, actor = _setup()
        _FakeAccountCls.auth_result = (object(), None)
        self._connect(session, actor, '"bob smith" "pass word"')
        self.assertEqual(
            _FakeAccountCls.auth_calls,
            [{"username": "bob smith", "password": "pass word"}],
        )

    def test_usage_on_missing_password(self):
        session, actor = _setup()
        self._connect(session, actor, "bob")
        self.assertTrue(any("Usage" in m for m in session.messages))

    def test_guest_login(self):
        session, actor = _setup()
        guest = object()
        _FakeGuestCls.auth_result = (guest, None)
        self._connect(session, actor, "guest")
        self.assertEqual(session.sessionhandler.logins, [(session, guest)])
        _FakeGuestCls.auth_result = (None, ["No guests available."])

    def test_guard_skips_logged_in_actor(self):
        session, actor = _setup()
        actor.account = object()  # logged in: the rule must abstain
        trace = self._connect(session, actor, "bob hunter2")
        self.assertFalse(session.messages)
        self.assertEqual(trace.carry_out_fired, 0)


# --- login_session (shared engine login op) --------------------------------------
class TestLoginSession(unittest.TestCase):
    """The reusable op behind ``connect`` and the web/REST ``login`` inputfunc."""

    def setUp(self):
        _FakeAccountCls.reset()

    def _login(self, session, name, password):
        with mock.patch.object(
            unloggedin_module, "class_from_module", side_effect=_fake_class_from_module
        ):
            return unloggedin_module.login_session(session, name, password)

    def test_success_logs_in_and_returns_account(self):
        session = LoginSession()
        account = object()
        _FakeAccountCls.auth_result = (account, None)
        result = self._login(session, "bob", "hunter2")
        self.assertIs(result, account)
        self.assertEqual(_FakeAccountCls.auth_calls, [{"username": "bob", "password": "hunter2"}])
        self.assertEqual(session.sessionhandler.logins, [(session, account)])

    def test_failure_messages_and_returns_none(self):
        session = LoginSession()
        result = self._login(session, "bob", "wrong")
        self.assertIsNone(result)
        self.assertTrue(any("Bad credentials" in m for m in session.messages))
        self.assertEqual(session.sessionhandler.logins, [])


# --- engine default binding ------------------------------------------------------
class TestServerSessionBinding(unittest.TestCase):
    """The engine's own ``ServerSession`` carries the login rules out of the box."""

    def test_engine_serversession_resolves_login_verbs(self):
        from evennia.actions.registry import rule_registry
        from evennia.server.serversession import ServerSession

        for action_type in (Connect, Create, Look, Quit):
            self.assertTrue(rule_registry.responds(ServerSession, action_type))


# --- look / quit (connect-screen rules) ------------------------------------------
class TestUnloggedLookQuit(unittest.TestCase):
    """The engine-default connect-screen ``look`` (re-render) and ``quit`` (drop)."""

    def _run(self, session, actor, action_cls, verb):
        action = action_cls.parse("", actor, verb=verb)
        return dispatch(action, actor, [session])

    def test_look_rerenders_connection_screen(self):
        session, actor = _setup()
        with mock.patch.object(unloggedin_module, "render_connection_screen") as render:
            self._run(session, actor, Look, "look")
        render.assert_called_once_with(session)

    def test_look_abstains_when_logged_in(self):
        session, actor = _setup()
        actor.account = object()
        with mock.patch.object(unloggedin_module, "render_connection_screen") as render:
            trace = self._run(session, actor, Look, "look")
        render.assert_not_called()
        self.assertEqual(trace.carry_out_fired, 0)

    def test_quit_disconnects(self):
        session, actor = _setup()
        self._run(session, actor, Quit, "quit")
        self.assertEqual([s for s, _ in session.sessionhandler.disconnects], [session])

    def test_quit_abstains_when_logged_in(self):
        session, actor = _setup()
        actor.account = object()
        trace = self._run(session, actor, Quit, "quit")
        self.assertEqual(session.sessionhandler.disconnects, [])
        self.assertEqual(trace.carry_out_fired, 0)


# --- create ----------------------------------------------------------------------
class TestCreate(unittest.TestCase):
    def setUp(self):
        _FakeAccountCls.reset()

    def _create(self, session, actor, raw, answer="y"):
        action = Create.parse(raw, actor, verb="create")

        def fake_input(actor_, prompt):
            d = Deferred()
            if prompt:
                session.msg(prompt)
            d.callback(answer)
            return d

        with mock.patch.object(engine_mod, "_get_input_deferred", side_effect=fake_input):
            return _patched_dispatch(session, actor, action)

    def test_create_confirmed(self):
        session, actor = _setup()
        _FakeAccountCls.create_result = (object(), None)
        self._create(session, actor, "bob hunter2", answer="y")
        self.assertEqual(_FakeAccountCls.create_calls, [{"username": "bob", "password": "hunter2"}])
        self.assertTrue(any("was created" in m for m in session.messages))

    def test_create_declined(self):
        session, actor = _setup()
        self._create(session, actor, "bob hunter2", answer="n")
        self.assertEqual(_FakeAccountCls.create_calls, [])
        self.assertTrue(any("Aborted" in m for m in session.messages))

    def test_create_errors_reported(self):
        session, actor = _setup()
        self._create(session, actor, "bob hunter2", answer="y")
        self.assertTrue(any("Could not create" in m for m in session.messages))

    def test_normalization_note(self):
        session, actor = _setup()
        _FakeAccountCls.normalized = "bobsmith"
        _FakeAccountCls.create_result = (object(), None)
        self._create(session, actor, '"bob smith" hunter2', answer="y")
        self.assertTrue(any("normalized" in m for m in session.messages))
        self.assertEqual(_FakeAccountCls.create_calls[0]["username"], "bobsmith")

    def test_usage_on_missing_password(self):
        session, actor = _setup()
        self._create(session, actor, "bob")
        self.assertTrue(any("Usage" in m for m in session.messages))

    def test_registration_disabled(self):
        session, actor = _setup()
        with mock.patch.object(settings, "NEW_ACCOUNT_REGISTRATION_ENABLED", False):
            self._create(session, actor, "bob hunter2")
        self.assertTrue(any("disabled" in m for m in session.messages))
        self.assertEqual(_FakeAccountCls.create_calls, [])


# --- info / encoding / screenreader / help -----------------------------------------
class TestUnloggedMisc(unittest.TestCase):
    def _run(self, session, actor, action_cls, raw="", switches=(), verb=None):
        action = action_cls.parse(raw, actor, switches=switches, verb=verb or "x")
        return dispatch(action, actor, [session])

    def test_info_outputs_mudinfo(self):
        session, actor = _setup()
        from evennia.utils import gametime

        with mock.patch.object(gametime, "SERVER_START_TIME", 1000000.0):
            self._run(session, actor, Info, verb="info")
        out = "\n".join(session.messages)
        self.assertIn("## BEGIN INFO 1.1", out)
        self.assertIn("## END INFO", out)

    def test_encoding_displays_current(self):
        session, actor = _setup()
        self._run(session, actor, Encoding, verb="encoding")
        self.assertTrue(any("utf-8" in m for m in session.messages))

    def test_encoding_set_valid(self):
        session, actor = _setup()
        self._run(session, actor, Encoding, raw="latin-1", verb="encoding")
        self.assertEqual(session.protocol_flags["ENCODING"], "latin-1")
        self.assertEqual(session.sessionhandler.synced, [session])

    def test_encoding_invalid_keeps_old(self):
        session, actor = _setup()
        self._run(session, actor, Encoding, raw="not-a-codec", verb="encoding")
        self.assertEqual(session.protocol_flags["ENCODING"], "utf-8")
        self.assertTrue(any("invalid" in m for m in session.messages))

    def test_encoding_clear(self):
        session, actor = _setup()
        session.protocol_flags["ENCODING"] = "latin-1"
        self._run(session, actor, Encoding, switches=("clear",), verb="encoding")
        self.assertEqual(session.protocol_flags["ENCODING"], "utf-8")
        self.assertTrue(any("cleared" in m for m in session.messages))

    def test_screenreader_toggles_and_syncs(self):
        session, actor = _setup()
        self._run(session, actor, Screenreader, verb="screenreader")
        self.assertTrue(session.protocol_flags["SCREENREADER"])
        self.assertEqual(session.sessionhandler.synced, [session])
        self.assertTrue(any("on" in m for m in session.messages))

    def test_unlogged_help_lists_commands(self):
        session, actor = _setup()
        self._run(session, actor, Help, verb="help")
        out = "\n".join(session.messages)
        self.assertIn("not yet logged into the game", out)
        self.assertIn("|wconnect|n", out)

    def test_unlogged_help_lists_look_and_quit(self):
        # look / quit are engine-bound connect-screen verbs, so the default
        # help advertises them.
        session, actor = _setup()
        self._run(session, actor, Help, verb="help")
        out = "\n".join(session.messages)
        self.assertIn("|wlook|n", out)
        self.assertIn("|wquit|n", out)

    def test_unlogged_help_staff_contact(self):
        session, actor = _setup()
        with mock.patch.object(settings, "STAFF_CONTACT_EMAIL", "admin@example.com"):
            self._run(session, actor, Help, verb="help")
        self.assertTrue(any("admin@example.com" in m for m in session.messages))

    def test_help_rule_abstains_when_logged_in(self):
        session, actor = _setup()
        actor.account = object()
        trace = self._run(session, actor, Help, verb="help")
        self.assertFalse(session.messages)
        self.assertEqual(trace.carry_out_fired, 0)


if __name__ == "__main__":
    unittest.main()
