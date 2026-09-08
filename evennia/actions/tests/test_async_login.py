"""Pending authentication must not attach disconnected or replaced sessions."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.core.exceptions import ImproperlyConfigured

from evennia.accounts import accounts
from evennia.accounts.accounts import DefaultAccount
from evennia.actions.default import unloggedin


class TestAsyncLogin(unittest.IsolatedAsyncioTestCase):
    """Keep session authority on the loop while credentials are checked elsewhere."""

    def setUp(self):
        """Bind one unauthenticated session and an awaitable account verifier."""
        self.session = SimpleNamespace(sessid=1, logged_in=False, address="127.0.0.1", msg=Mock())
        self.sessions = {1: self.session}
        self.session.sessionhandler = SimpleNamespace(get=self.sessions.get, login=Mock())
        self.account = object()
        self.auth = SimpleNamespace(aauthenticate=AsyncMock(return_value=(self.account, [])))
        patcher = patch.object(unloggedin, "class_from_module", return_value=self.auth)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_success(self):
        """A live session receives the verified account."""
        result = await unloggedin.login_session(self.session, "player", "password")
        self.assertIs(result, self.account)
        self.session.sessionhandler.login.assert_called_once_with(self.session, self.account)

    async def test_disconnected_session_is_not_logged_in(self):
        """A removed session cannot be resurrected by worker completion."""

        async def verify(**kwargs):
            """Disconnect before authentication completes."""
            self.sessions.clear()
            return self.account, []

        self.auth.aauthenticate.side_effect = verify
        self.assertIsNone(await unloggedin.login_session(self.session, "player", "password"))
        self.session.sessionhandler.login.assert_not_called()
        self.session.msg.assert_not_called()

    async def test_reused_session_id_is_not_logged_in(self):
        """A different connection with the same ID cannot inherit the result."""

        async def verify(**kwargs):
            """Replace the session before authentication completes."""
            self.sessions[1] = object()
            return self.account, []

        self.auth.aauthenticate.side_effect = verify
        await unloggedin.login_session(self.session, "player", "password")
        self.session.sessionhandler.login.assert_not_called()

    async def test_duplicate_requests_only_verify_once(self):
        """One connection cannot queue overlapping password checks."""
        entered = asyncio.Event()
        release = asyncio.Event()

        async def verify(**kwargs):
            """Hold the first verification while another request arrives."""
            entered.set()
            await release.wait()
            return self.account, []

        self.auth.aauthenticate.side_effect = verify
        first = asyncio.create_task(unloggedin.login_session(self.session, "player", "password"))
        await entered.wait()
        try:
            await unloggedin.login_session(self.session, "player", "password")
            self.auth.aauthenticate.assert_awaited_once()
        finally:
            release.set()
            await first
        self.session.sessionhandler.login.assert_called_once()

    async def test_failure_allows_another_attempt(self):
        """A rejected password releases the session's pending marker."""
        self.auth.aauthenticate.side_effect = [(None, ["Bad credentials"]), (self.account, [])]
        await unloggedin.login_session(self.session, "player", "wrong")
        await unloggedin.login_session(self.session, "player", "correct")
        self.assertEqual(self.auth.aauthenticate.await_count, 2)
        self.session.sessionhandler.login.assert_called_once()

    async def test_existing_login_is_not_replaced(self):
        """Another successful login wins over an outstanding verification."""

        async def verify(**kwargs):
            """Attach an account before the pending check returns."""
            self.session.logged_in = True
            return self.account, []

        self.auth.aauthenticate.side_effect = verify
        await unloggedin.login_session(self.session, "player", "password")
        self.session.sessionhandler.login.assert_not_called()

    async def test_legacy_command_awaits_login(self):
        """Legacy command users take the same nonblocking login path."""
        from evennia.commands.default.unloggedin import CmdUnconnectedConnect

        command = CmdUnconnectedConnect()
        command.caller = self.session
        command.args = '"player" "password"'
        await command.func()
        self.auth.aauthenticate.assert_awaited_once()
        self.session.sessionhandler.login.assert_called_once()

    async def test_inputfunc_schedules_login(self):
        """The synchronous input dispatcher starts and owns the login coroutine."""
        from evennia.server import inputfuncs
        from evennia.utils import clock

        with patch.object(clock, "run_coroutine", side_effect=asyncio.create_task):
            task = inputfuncs.login(self.session, name="player", password="password")
        await task
        self.session.sessionhandler.login.assert_called_once()


class TestAsyncAccountPolicy(unittest.IsolatedAsyncioTestCase):
    """The asynchronous path preserves account policy and failed-login hooks."""

    def setUp(self):
        """Replace only external policy stores and the Django backend boundary."""
        self.patches = [
            patch.object(accounts, "LOGIN_THROTTLE"),
            patch.object(DefaultAccount, "is_banned", return_value=False),
            patch.object(accounts, "aauthenticate", new_callable=AsyncMock),
            patch.object(accounts.logger, "log_sec"),
        ]
        self.throttle, self.banned, self.verify, self.log = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.throttle.check.return_value = False
        self.verify.return_value = object()

    async def test_throttled_attempt_never_starts_verification(self):
        """Existing IP throttles prevent expensive password work."""
        self.throttle.check.return_value = True
        account, errors = await DefaultAccount.aauthenticate("player", "password", ip="127.0.0.1")
        self.assertIsNone(account)
        self.assertTrue(errors)
        self.verify.assert_not_awaited()

    async def test_banned_attempt_never_starts_verification(self):
        """Existing bans continue to reject direct game logins."""
        self.banned.return_value = True
        account, errors = await DefaultAccount.aauthenticate("player", "password", ip="127.0.0.1")
        self.assertIsNone(account)
        self.assertTrue(errors)
        self.verify.assert_not_awaited()

    async def test_ban_added_during_verification_blocks_login(self):
        """Ban policy is rechecked when the password worker returns."""
        self.banned.side_effect = [False, True]
        account, errors = await DefaultAccount.aauthenticate("player", "password", ip="127.0.0.1")
        self.assertIsNone(account)
        self.assertTrue(errors)
        self.verify.assert_awaited_once()

    async def test_failed_login_updates_throttle_and_calls_hook(self):
        """The asynchronous failure path retains logging, signals and account hooks."""
        self.verify.return_value = None
        target = Mock()
        session = object()
        with (
            patch.object(accounts.AccountDB.objects, "get_account_from_name", return_value=target),
            patch.object(accounts.SIGNAL_ACCOUNT_POST_LOGIN_FAIL, "send") as signal,
        ):
            account, errors = await DefaultAccount.aauthenticate(
                "player", "wrong", ip="127.0.0.1", session=session
            )
        self.assertIsNone(account)
        self.assertTrue(errors)
        self.throttle.update.assert_called_once()
        signal.assert_called_once_with(sender=target, session=session)
        target.at_failed_login.assert_called_once_with(session)

    async def test_sync_only_custom_policy_cannot_be_bypassed(self):
        """An inherited async method must not bypass a game's sync-only policy."""

        class CustomPolicy:
            """Model the classmethod overrides of a custom account typeclass."""

            @classmethod
            def authenticate(cls, *args, **kwargs):
                """A game-specific authentication refusal."""
                return None, ["Denied by custom policy"]

            aauthenticate = classmethod(DefaultAccount.aauthenticate.__func__)
            _authentication_errors = classmethod(lambda cls, *args: [])
            _authentication_result = classmethod(lambda cls, account, *args: (account, []))

        with self.assertRaises(ImproperlyConfigured):
            await CustomPolicy.aauthenticate("player", "password")
        self.verify.assert_not_awaited()
