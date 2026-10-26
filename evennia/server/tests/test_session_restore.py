"""
Sessions that arrive already authenticated are logged in one loop turn each.

A Portal that outlives a Server restart (or restarts with it) hands the Server every
socket that is waiting, and the bus admits them in one callback. The dear half of a
login (`account.at_post_login`, which puppets the character and shows the room) ran for
all of them back to back: about 20 sessions held the reactor for 2.9 s on production.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from evennia.server import sessionhandler
from evennia.server.bus_sessions import SessionReconciler
from evennia.server.sessionhandler import ServerSessionHandler


class StubSession:
    """The parts of a Server session that admission and login touch."""

    def __init__(self):
        self.sessid = None
        self.uid = None
        self.uname = None
        self.logged_in = False
        self.account = None
        self.address = "local"
        self.sessionhandler = None
        self.received = []
        self.disconnected = None

    def load_sync_data(self, data):
        for key in ("sessid", "uid", "uname", "logged_in", "address"):
            if key in data:
                setattr(self, key, data[key])

    def at_sync(self):
        pass

    def at_login(self, account):
        self.account = account
        self.uid = account.uid
        self.logged_in = True

    def at_disconnect(self, reason=None):
        self.disconnected = reason

    def log(self, message):
        pass

    def data_in(self, **kwargs):
        self.received.append(kwargs)

    def msg(self, *args, **kwargs):
        pass


def _account(uid):
    account = Mock(uid=uid, id=uid)
    account.at_pre_login.return_value = None
    account.db.FIRST_LOGIN = False
    return account


def _sync(sid, uid=None):
    """What the Portal says about a socket; ``uid`` makes it an authenticated one."""
    return {
        "sessid": sid,
        "_socket_id": f"socket-{sid}",
        "address": "local",
        "protocol_flags": {},
        "_protocol_auth": {"uid": uid, "uname": f"user{uid}", "logged_in": True} if uid else {},
    }


@override_settings(MULTISESSION_MODE=2)
class RestoreTestCase(SimpleTestCase):
    """A real handler over stub sessions, with the loop's callbacks held by the test."""

    def setUp(self):
        self.handler = ServerSessionHandler()
        self.accounts = {}
        self.callbacks = []
        for patcher in (
            patch.object(sessionhandler, "delayed_import"),
            patch.object(sessionhandler, "_ServerSession", StubSession),
            patch.object(sessionhandler, "_AccountDB", SimpleNamespace(objects=self)),
            patch.object(sessionhandler, "evennia"),
            patch.object(sessionhandler, "delay"),
            # a game's logout receivers reach the database, which this test has none of
            patch.object(sessionhandler, "SIGNAL_ACCOUNT_POST_LAST_LOGOUT"),
            patch.object(sessionhandler, "SIGNAL_ACCOUNT_POST_LOGOUT"),
            patch("evennia.utils.clock.call_later", side_effect=self._call_later),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bus = sessionhandler.evennia.EVENNIA_SERVER_SERVICE.portal_bus

    # stands in for AccountDB.objects
    def get_account_from_uid(self, uid):
        return self.accounts.setdefault(uid, _account(uid))

    def _call_later(self, seconds, fn, *args, **kwargs):
        self.callbacks.append(fn)

    def run_one_turn(self):
        """Run the callback the loop would run next."""
        self.callbacks.pop(0)()

    def account(self, uid):
        return self.get_account_from_uid(uid)

    def post_logins(self):
        return [uid for uid, account in self.accounts.items() if account.at_post_login.called]


class TestDeferredLogin(RestoreTestCase):
    def test_a_plain_connect_logs_in_at_once(self):
        self.handler.portal_connect(_sync(1) | {"uid": 7, "logged_in": True})

        self.assertEqual(self.post_logins(), [7])
        self.assertEqual(self.callbacks, [])

    def test_a_deferred_login_is_half_done_at_once_and_finished_in_a_turn(self):
        self.handler.portal_connect(_sync(1) | {"uid": 7, "logged_in": True}, defer_login=True)
        session = self.handler[1]
        account = self.account(7)

        # The session is the account's and the Portal has been told; nothing is puppeted.
        self.assertTrue(session.logged_in)
        self.assertIs(session.account, account)
        self.assertTrue(account.is_connected)
        self.assertEqual(self.bus.send_AdminServer2Portal.call_count, 1)
        account.at_post_login.assert_not_called()

        self.run_one_turn()

        account.at_post_login.assert_called_once_with(session=session)

    def test_a_session_that_is_not_authenticated_is_not_deferred(self):
        self.handler.portal_connect(_sync(1), defer_login=True)

        self.assertIn(1, self.handler)
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.handler._restoring, {})

    def test_each_login_has_a_callback_of_its_own(self):
        for sid in (1, 2, 3):
            self.handler.portal_connect(
                _sync(sid) | {"uid": sid, "logged_in": True}, defer_login=True
            )
        self.assertEqual(len(self.callbacks), 1, "one callback asked for, not one per session")

        seen = []
        while self.callbacks:
            self.run_one_turn()
            seen.append(self.post_logins())

        self.assertEqual(seen, [[1], [1, 2], [1, 2, 3]])

    def test_the_first_login_signal_follows_the_session_count_of_the_first_half(self):
        from evennia.server.signals import SIGNAL_ACCOUNT_POST_FIRST_LOGIN

        firsts = []
        receiver = lambda sender, **kw: firsts.append(kw["session"].sessid)  # noqa: E731
        SIGNAL_ACCOUNT_POST_FIRST_LOGIN.connect(receiver, weak=False)
        self.addCleanup(SIGNAL_ACCOUNT_POST_FIRST_LOGIN.disconnect, receiver)
        # Two sockets of one account: the first is the account's first session.
        for sid in (1, 2):
            self.handler.portal_connect(
                _sync(sid) | {"uid": 7, "logged_in": True}, defer_login=True
            )

        while self.callbacks:
            self.run_one_turn()

        self.assertEqual(firsts, [1])

    def test_a_vetoed_login_is_never_queued(self):
        self.account(7).at_pre_login.return_value = False
        with patch.object(self.handler, "disconnect") as disconnect:
            self.handler.portal_connect(_sync(1) | {"uid": 7, "logged_in": True}, defer_login=True)

        disconnect.assert_called_once()
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.handler._restoring, {})

    def test_the_ordinary_login_is_unchanged(self):
        session = StubSession()
        session.sessid = 1
        self.handler[1] = session
        account = self.account(7)

        self.handler.login(session, account)

        account.at_post_login.assert_called_once_with(session=session)
        self.assertTrue(session.logged_in)
        self.assertEqual(self.callbacks, [])

    def test_a_login_that_is_already_done_does_not_run_again(self):
        session = StubSession()
        session.sessid = 1
        session.logged_in = True
        self.handler[1] = session
        account = self.account(7)

        self.handler.login(session, account)

        account.at_post_login.assert_not_called()


class TestHeldInput(RestoreTestCase):
    def _defer(self, sid=1, uid=7):
        self.handler.portal_connect(_sync(sid) | {"uid": uid, "logged_in": True}, defer_login=True)
        return self.handler[sid]

    def test_input_waits_for_the_login_and_then_arrives_in_order(self):
        session = self._defer()

        self.handler.data_in(session, text=[["look"], {}])
        self.handler.data_in(session, text=[["north"], {}])
        self.assertEqual(session.received, [])

        self.run_one_turn()

        self.assertEqual(session.received, [{"text": [["look"], {}]}, {"text": [["north"], {}]}])

    def test_input_after_the_login_goes_straight_to_the_session(self):
        session = self._defer()
        self.run_one_turn()

        self.handler.data_in(session, text=[["look"], {}])

        self.assertEqual(session.received, [{"text": [["look"], {}]}])

    def test_input_for_another_session_is_not_held(self):
        waiting = self._defer(1, 7)
        other = StubSession()
        other.sessid = 2
        self.handler[2] = other

        self.handler.data_in(other, text=[["look"], {}])

        self.assertEqual(other.received, [{"text": [["look"], {}]}])
        self.assertEqual(waiting.received, [])

    def test_a_session_that_goes_while_it_waits_never_finishes_its_login(self):
        session = self._defer()
        self.handler.data_in(session, text=[["look"], {}])

        self.handler.disconnect(session)
        self.run_one_turn()

        self.assertFalse(self.account(7).at_post_login.called)
        self.assertEqual(session.received, [])
        self.assertEqual(self.handler._restoring, {})

    def test_a_number_reused_by_a_new_session_is_not_held_for_the_old_one(self):
        old = self._defer()
        self.handler.disconnect(old)
        new = StubSession()
        new.sessid = 1
        self.handler[1] = new

        self.handler.data_in(new, text=[["look"], {}])

        self.assertEqual(new.received, [{"text": [["look"], {}]}])

    def test_a_stale_wait_for_a_reused_number_does_not_hold_the_new_session(self):
        old = self._defer()
        # The old session is replaced without going through disconnect.
        new = StubSession()
        new.sessid = 1
        self.handler[1] = new

        self.handler.data_in(new, text=[["look"], {}])
        self.run_one_turn()

        self.assertEqual(new.received, [{"text": [["look"], {}]}])
        self.assertEqual(old.received, [])
        self.assertFalse(self.account(7).at_post_login.called)

    def test_clearing_the_handler_forgets_the_waiting(self):
        session = self._defer()
        self.handler.data_in(session, text=[["look"], {}])

        self.handler.clear()
        self.run_one_turn()

        self.assertEqual(self.handler._restoring, {})
        self.assertFalse(self.account(7).at_post_login.called)

    def test_a_flood_while_waiting_is_capped(self):
        session = self._defer()

        for number in range(sessionhandler._MAX_HELD_INPUT + 50):
            self.handler.data_in(session, text=[[str(number)], {}])
        self.run_one_turn()

        self.assertEqual(len(session.received), sessionhandler._MAX_HELD_INPUT)
        self.assertEqual(session.received[0], {"text": [["0"], {}]})


class TestFailedLogin(RestoreTestCase):
    def test_a_login_that_fails_drops_that_session_and_the_next_still_runs(self):
        for sid in (1, 2):
            self.handler.portal_connect(
                _sync(sid) | {"uid": sid, "logged_in": True}, defer_login=True
            )
        self.account(1).at_post_login.side_effect = RuntimeError("no body")

        with patch.object(sessionhandler, "log_trace") as trace:
            self.run_one_turn()
        self.run_one_turn()

        trace.assert_called_once()
        self.assertNotIn(1, self.handler)
        self.assertIn(2, self.handler)
        self.assertTrue(self.account(2).at_post_login.called)
        self.assertEqual(self.callbacks, [])

    def test_held_input_of_a_failed_login_is_dropped(self):
        self.handler.portal_connect(_sync(1) | {"uid": 1, "logged_in": True}, defer_login=True)
        session = self.handler[1]
        self.handler.data_in(session, text=[["look"], {}])
        self.account(1).at_post_login.side_effect = RuntimeError("no body")

        with patch.object(sessionhandler, "log_trace"):
            self.run_one_turn()

        self.assertEqual(session.received, [])
        self.assertEqual(self.handler._restoring, {})

    def test_input_that_fails_when_replayed_does_not_stop_the_rest(self):
        self.handler.portal_connect(_sync(1) | {"uid": 1, "logged_in": True}, defer_login=True)
        session = self.handler[1]
        seen = []

        def data_in(**kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                raise ValueError("bad input")

        session.data_in = data_in
        self.handler.data_in(session, text=[["one"], {}])
        self.handler.data_in(session, text=[["two"], {}])

        with patch.object(sessionhandler, "log_trace") as trace:
            self.run_one_turn()

        trace.assert_called_once()
        self.assertEqual(len(seen), 2)

    def test_a_login_that_disconnects_its_own_session_leaves_nothing_behind(self):
        self.handler.portal_connect(_sync(1) | {"uid": 1, "logged_in": True}, defer_login=True)
        session = self.handler[1]
        self.account(1).at_post_login.side_effect = lambda session: self.handler.disconnect(session)

        self.run_one_turn()

        self.assertNotIn(1, self.handler)
        self.assertEqual(self.handler._restoring, {})


class TestReconcileDefersLogins(RestoreTestCase):
    """The bus asks for deferral exactly when several authenticated sockets arrive."""

    def setUp(self):
        super().setUp()
        self.reconciler = SessionReconciler(self.handler)

    def test_several_authenticated_sockets_log_in_one_to_a_turn(self):
        snapshot = {sid: _sync(sid, uid=sid) for sid in (1, 2, 3, 4)}

        self.reconciler.reconcile(snapshot, "portal-a")

        self.assertEqual(sorted(self.handler), [1, 2, 3, 4])
        self.assertEqual(self.post_logins(), [])
        seen = []
        while self.callbacks:
            self.run_one_turn()
            seen.append(len(self.post_logins()))
        self.assertEqual(seen, [1, 2, 3, 4])

    def test_one_authenticated_socket_logs_in_during_the_call(self):
        self.reconciler.reconcile({1: _sync(1, uid=1)}, "portal-a")

        self.assertEqual(self.post_logins(), [1])
        self.assertEqual(self.callbacks, [])

    def test_sockets_that_are_not_authenticated_do_not_count(self):
        snapshot = {1: _sync(1, uid=1), 2: _sync(2), 3: _sync(3)}

        self.reconciler.reconcile(snapshot, "portal-a")

        self.assertEqual(self.post_logins(), [1])
        self.assertEqual(self.callbacks, [])

    def test_a_socket_that_claims_a_login_without_an_account_does_not_count(self):
        claims = {**_sync(2), "_protocol_auth": {"logged_in": True}}
        snapshot = {1: _sync(1, uid=1), 2: claims}

        self.reconciler.reconcile(snapshot, "portal-a")

        self.assertEqual(self.post_logins(), [1])
        self.assertEqual(self.callbacks, [])

    def test_sockets_already_admitted_do_not_count(self):
        self.reconciler.reconcile({1: _sync(1, uid=1)}, "portal-a")
        snapshot = {1: _sync(1, uid=1), 2: _sync(2, uid=2)}

        self.reconciler.reconcile(snapshot, "portal-a")

        self.assertEqual(self.post_logins(), [1, 2])
        self.assertEqual(self.callbacks, [])

    def test_the_state_sent_back_to_the_portal_is_true_while_logins_wait(self):
        snapshot = {sid: _sync(sid, uid=sid) for sid in (1, 2)}

        self.reconciler.reconcile(snapshot, "portal-a")
        state = self.reconciler.server_state()

        self.assertEqual(state["closed"], {})
        self.assertEqual(
            {sid: (data["uid"], data["logged_in"]) for sid, data in state["sessions"].items()},
            {1: (1, True), 2: (2, True)},
        )

    def test_a_socket_that_closes_while_it_waits_is_dropped_cleanly(self):
        snapshot = {sid: _sync(sid, uid=sid) for sid in (1, 2)}
        self.reconciler.reconcile(snapshot, "portal-a")

        self.reconciler.reconcile({2: snapshot[2]}, "portal-a")
        while self.callbacks:
            self.run_one_turn()

        self.assertEqual(sorted(self.handler), [2])
        self.assertEqual(self.post_logins(), [2])
