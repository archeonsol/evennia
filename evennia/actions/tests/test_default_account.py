"""Tests for the engine-shipped account actions (``evennia.actions.default.account``).

The action-engine analogue of the ``CmdOption``/``CmdPassword``/
``CmdNewPassword`` command tests: each verb dispatches through a real
:class:`~evennia.actions.engine.RuleEngine` with the account as provider
(the account-shell scope), asserting messages, protocol-flag updates,
password changes, and gates.
"""

import unittest
from unittest import mock

from evennia.actions.default.account import (
    DefaultAccountRules,
    Option,
    Password,
    UserPassword,
)
from evennia.actions.tests.fakes import FakeAccount, FakeSession, dispatch, make_actor


class Account(DefaultAccountRules, FakeAccount):
    """Provider fixture: an account carrying the account-shell rules."""


def _setup(capabilities=(), flags=None):
    account = Account(capabilities=capabilities)
    session = FakeSession(flags=flags)
    actor = make_actor(None, session=session, account=account)
    return account, session, actor


# --- @option -------------------------------------------------------------------
class TestOption(unittest.TestCase):
    def _option(self, account, actor, raw, switches=()):
        action = Option.parse(raw, actor, switches=switches, verb="@option")
        return dispatch(action, actor, [account])

    def test_no_session_is_noop(self):
        account, _, _ = _setup()
        actor = make_actor(None, session=None, account=account)
        action = Option.parse("", actor, verb="@option")
        dispatch(action, actor, [account])
        self.assertFalse(account.messages)

    def test_display_settings(self):
        account, _, actor = _setup()
        self._option(account, actor, "")
        self.assertTrue(any("Client settings" in m for m in account.messages))

    def test_display_settings_tolerates_structured_flags(self):
        """Non-string protocol flags must not break the settings table.

        The webclient records handshake metadata on the session alongside the
        client options -- ``HTTP_ORDER`` is a list, ``HTTP_FP`` a dict -- and a
        bare ``@option`` renders every flag into an EvTable, whose cells are
        text-only.
        """
        account, _, actor = _setup(
            flags={
                "ANSI": True,
                "HTTP_ORDER": ["Host", "User-Agent"],
                "HTTP_FP": {"user-agent": "curl/8.0"},
                "PEER_IP": None,
            }
        )
        self._option(account, actor, "")
        self.assertTrue(any("Client settings" in m for m in account.messages))

    def test_set_option(self):
        account, session, actor = _setup()
        self._option(account, actor, "ANSI = off")
        self.assertTrue(any("was changed from" in m for m in account.messages))
        self.assertEqual(session.updated_flags, [{"ANSI": False}])

    def test_set_option_unchanged(self):
        account, session, actor = _setup()
        self._option(account, actor, "ANSI = on")
        self.assertTrue(any("was kept as" in m for m in account.messages))

    def test_unknown_option(self):
        account, _, actor = _setup()
        self._option(account, actor, "BOGUS = on")
        self.assertTrue(any("No option named" in m for m in account.messages))

    def test_lhs_without_rhs_usage(self):
        account, _, actor = _setup()
        self._option(account, actor, "ANSI")
        self.assertTrue(any("Usage" in m for m in account.messages))

    def test_screenwidth_disables_autoresize(self):
        account, session, actor = _setup()
        self._option(account, actor, "SCREENWIDTH = 100")
        self.assertFalse(session.protocol_flags["AUTORESIZE"])
        self.assertEqual(session.updated_flags, [{"SCREENWIDTH": {0: 100}}])

    def test_save_all_options(self):
        account, session, actor = _setup()
        self._option(account, actor, "", switches=("save",))
        self.assertEqual(
            account.db._saved_protocol_flags,
            {k: v for k, v in session.protocol_flags.items() if k != "SCREENREADER"},
        )
        self.assertTrue(any("Saved all options" in m for m in account.messages))

    def test_screenreader_saves_without_the_save_switch(self):
        account, _, actor = _setup()
        self._option(account, actor, "SCREENREADER = on")
        self.assertEqual(account.db._saved_protocol_flags, {"SCREENREADER": True})
        self.assertTrue(any("Saved option SCREENREADER" in m for m in account.messages))

    def test_screenreader_off_is_stored_as_absence(self):
        account, _, actor = _setup()
        account.db._saved_protocol_flags = {"SCREENREADER": True, "ANSI": True}
        self._option(account, actor, "SCREENREADER = off")
        self.assertEqual(account.db._saved_protocol_flags, {"ANSI": True})

    def test_screenreader_off_on_a_quiet_session_still_clears_the_save(self):
        account, _, actor = _setup()
        account.db._saved_protocol_flags = {"SCREENREADER": True}
        self._option(account, actor, "SCREENREADER = off")
        self.assertEqual(account.db._saved_protocol_flags, {})

    def test_save_all_drops_a_saved_screenreader_off(self):
        account, _, actor = _setup()
        self._option(account, actor, "", switches=("save",))
        self.assertNotIn("SCREENREADER", account.db._saved_protocol_flags)
        self.assertIn("ANSI", account.db._saved_protocol_flags)

    def test_clear_saved_options(self):
        account, _, actor = _setup()
        account.db._saved_protocol_flags = {"ANSI": True}
        self._option(account, actor, "", switches=("clear",))
        self.assertEqual(account.db._saved_protocol_flags, {})

    def test_save_single_option(self):
        account, _, actor = _setup()
        self._option(account, actor, "ANSI = off", switches=("save",))
        self.assertEqual(account.attributes.get("_saved_protocol_flags"), {"ANSI": False})
        self.assertTrue(any("Saved option ANSI" in m for m in account.messages))


# --- @password -----------------------------------------------------------------
class TestPassword(unittest.TestCase):
    def _password(self, account, actor, raw):
        action = Password.parse(raw, actor, verb="@password")
        return dispatch(action, actor, [account])

    def test_usage_without_rhs(self):
        account, _, actor = _setup()
        self._password(account, actor, "oldonly")
        self.assertTrue(any("Usage" in m for m in account.messages))

    def test_wrong_old_password(self):
        account, _, actor = _setup()
        self._password(account, actor, "wrong = newpass")
        self.assertTrue(any("isn't correct" in m for m in account.messages))
        self.assertEqual(account.password, "secret")

    def test_invalid_new_password(self):
        account, _, actor = _setup()
        account.valid_password = False
        self._password(account, actor, "secret = x")
        self.assertTrue(any("failed validation" in m for m in account.messages))
        self.assertEqual(account.password, "secret")

    def test_password_changed(self):
        account, _, actor = _setup()
        self._password(account, actor, "secret = hunter2")
        self.assertEqual(account.password, "hunter2")
        self.assertTrue(account.saved)
        self.assertTrue(any("Password changed" in m for m in account.messages))

    def test_new_password_may_contain_a_comma(self):
        account, _, actor = _setup()
        self._password(account, actor, "secret = new,pass")
        self.assertEqual(account.password, "new,pass")

    def test_not_gated_by_capability(self):
        account, _, actor = _setup()
        trace = self._password(account, actor, "secret = hunter2")
        self.assertEqual(account.password, "hunter2")
        self.assertEqual(trace.carry_out_gated, 0)


# --- @userpassword ---------------------------------------------------------------
class TestUserPassword(unittest.TestCase):
    def _userpassword(self, account, actor, raw, matches=(), beyond=frozenset()):
        action = UserPassword.parse(raw, actor, verb="@userpassword")
        with (
            mock.patch("evennia.utils.search.search_account", return_value=list(matches)),
            mock.patch("evennia.authorization.service.capabilities_beyond", return_value=beyond),
        ):
            return dispatch(action, actor, [account])

    def test_usage_without_rhs(self):
        account, _, actor = _setup(capabilities=("engine.account.password",))
        self._userpassword(account, actor, "bob")
        self.assertTrue(any("Usage" in m for m in account.messages))

    def test_sets_target_password(self):
        account, _, actor = _setup(capabilities=("engine.account.password",))
        target = FakeAccount(key="bob", password="old")
        self._userpassword(account, actor, "bob = newpass", matches=[target])
        self.assertEqual(target.password, "newpass")
        self.assertTrue(target.saved)
        self.assertTrue(any("new password set" in m for m in account.messages))
        self.assertTrue(any("changed your password" in m for m in target.messages))

    def test_unknown_account_reported(self):
        account, _, actor = _setup(capabilities=("engine.account.password",))
        self._userpassword(account, actor, "*nobody = newpass")
        self.assertTrue(
            any("Could not find an account named 'nobody'" in m for m in account.messages)
        )

    def test_ambiguous_account_reported(self):
        account, _, actor = _setup(capabilities=("engine.account.password",))
        first, second = FakeAccount(key="bob"), FakeAccount(key="bobby")
        first.id, second.id = 1, 2
        self._userpassword(account, actor, "bob = newpass", matches=[first, second])
        self.assertEqual(first.password, "secret")
        self.assertTrue(any("More than one account" in m for m in account.messages))

    def test_refuses_target_with_more_capabilities(self):
        account, _, actor = _setup(capabilities=("engine.account.password",))
        target = FakeAccount(key="op", password="old")
        self._userpassword(
            account, actor, "op = newpass", matches=[target], beyond={"engine.runtime.manage"}
        )
        self.assertEqual(target.password, "old")
        self.assertTrue(any("holds capabilities you don't" in m for m in account.messages))

    def test_invalid_password_reported(self):
        account, _, actor = _setup(capabilities=("engine.account.password",))
        target = FakeAccount(key="bob", valid_password=False)
        self._userpassword(account, actor, "bob = x", matches=[target])
        self.assertEqual(target.password, "secret")
        self.assertEqual(
            [m for m in account.messages if "failed validation" in m],
            ["Password failed validation."],
        )

    def test_gated_for_builder(self):
        account, _, actor = _setup(capabilities=("engine.world.build",))
        trace = self._userpassword(account, actor, "bob = newpass")
        self.assertFalse(account.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)

    def test_gated_for_moderator_without_password_key(self):
        account, _, actor = _setup(capabilities=("engine.moderation.manage",))
        trace = self._userpassword(account, actor, "bob = newpass")
        self.assertFalse(account.messages)
        self.assertGreaterEqual(trace.carry_out_gated, 1)


if __name__ == "__main__":
    unittest.main()
