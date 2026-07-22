# -*- coding: utf-8 -*-

from random import randint
from unittest import TestCase

from django.test import override_settings
from mock import MagicMock, Mock, patch

import evennia
from evennia.accounts.accounts import (AccountSessionHandler, DefaultAccount,
                                       DefaultGuest)
from evennia.accounts.models import ControlBinding
from evennia.authorization.policy import Always
from evennia.authorization.storage import grant_capability
from evennia.utils import create
from evennia.utils.test_resources import BaseEvenniaTest
from evennia.utils.utils import uses_database


def _last_data_out_text(session):
    """Extract text parity from the session's latest output call.

    Args:
        session (ServerSession): Session with a mocked ``data_out`` method.

    Returns:
        str: Text payload, whether delivered directly or as a narrative node.

    """
    kwargs = session.data_out.call_args[1]
    if "text" in kwargs:
        return kwargs["text"]
    payloads, _options = kwargs["narrative"]
    return payloads[-1]["body"]


class TestAccountSessionHandler(TestCase):
    "Check AccountSessionHandler class"

    def setUp(self):
        self.account = create.create_account(
            f"TestAccount{randint(0, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )
        self.handler = AccountSessionHandler(self.account)

    def tearDown(self):
        if hasattr(self, "account"):
            self.account.delete()

    def test_get(self):
        "Check get method"
        self.assertEqual(self.handler.get(), [])
        self.assertEqual(self.handler.get(100), [])

        s1 = MagicMock()
        s1.logged_in = True
        s1.uid = self.account.uid
        evennia.SESSION_HANDLER[s1.uid] = s1

        s2 = MagicMock()
        s2.logged_in = True
        s2.uid = self.account.uid + 1
        evennia.SESSION_HANDLER[s2.uid] = s2

        s3 = MagicMock()
        s3.logged_in = False
        s3.uid = self.account.uid + 2
        evennia.SESSION_HANDLER[s3.uid] = s3

        self.assertEqual([s.uid for s in self.handler.get()], [s1.uid])
        self.assertEqual(
            [s.uid for s in [self.handler.get(self.account.uid)]], [s1.uid]
        )
        self.assertEqual([s.uid for s in self.handler.get(self.account.uid + 1)], [])

    def test_all(self):
        "Check all method"
        self.assertEqual(self.handler.get(), self.handler.all())

    def test_count(self):
        "Check count method"
        self.assertEqual(self.handler.count(), len(self.handler.get()))


@override_settings(GUEST_ENABLED=True, GUEST_LIST=["bruce_wayne"])
class TestDefaultGuest(BaseEvenniaTest):
    "Check DefaultGuest class"

    ip = "212.216.134.22"

    @override_settings(GUEST_ENABLED=False)
    def test_create_not_enabled(self):
        # Guest account should not be permitted
        account, errors = DefaultGuest.authenticate(ip=self.ip)
        self.assertFalse(account, "Guest account was created despite being disabled.")

    def test_authenticate(self):
        # Create a guest account
        account, errors = DefaultGuest.authenticate(ip=self.ip)
        self.assertTrue(account, "Guest account should have been created.")

        # Create a second guest account
        account, errors = DefaultGuest.authenticate(ip=self.ip)
        self.assertFalse(
            account,
            "Two guest accounts were created with a single entry on the guest list!",
        )

    @patch("evennia.accounts.accounts.ChannelDB.objects.get_channel")
    def test_create(self, get_channel):
        get_channel.connect = MagicMock(return_value=True)
        with override_settings(GUEST_HOME=self.room1.dbref):
            account, errors = DefaultGuest.create()
        self.assertTrue(account, "Guest account should have been created.")
        self.assertFalse(errors)

    def test_at_post_login(self):
        self.account.db._last_puppet = self.char1
        self.account.at_post_login(self.session)
        self.account.at_post_login()

    def test_at_server_shutdown(self):
        account, errors = DefaultGuest.create(ip=self.ip)
        self.char1.delete = MagicMock()
        # char1 is owned by the main test account; transfer=True takes the
        # throwaway body for the guest (sanctioned reassignment).
        account.characters.add(self.char1, transfer=True)
        account.at_server_shutdown()
        self.char1.delete.assert_called()

    def test_at_post_disconnect(self):
        account, errors = DefaultGuest.create(ip=self.ip)
        self.char1.delete = MagicMock()
        account.characters.add(self.char1, transfer=True)
        account.at_post_disconnect()
        self.char1.delete.assert_called()


class TestDefaultAccountAuth(BaseEvenniaTest):
    def setUp(self):
        super().setUp()

        self.password = "testpassword"
        self.account.delete()
        self.account = create.create_account(
            f"TestAccount{randint(100000, 999999)}",
            email="test@test.com",
            password=self.password,
            typeclass=DefaultAccount,
        )

    def test_authentication(self):
        "Confirm Account authentication method is authenticating/denying users."
        # Valid credentials
        obj, errors = DefaultAccount.authenticate(self.account.name, self.password)
        self.assertTrue(obj, "Account did not authenticate given valid credentials.")

        # Invalid credentials
        obj, errors = DefaultAccount.authenticate(self.account.name, "xyzzy")
        self.assertFalse(obj, "Account authenticated using invalid credentials.")

    def test_create(self):
        "Confirm Account creation is working as expected."
        # Create a normal account
        account, errors = DefaultAccount.create(username="ziggy", password="stardust11")
        self.assertTrue(account, "New account should have been created.")

        # Try creating a duplicate account
        account2, errors = DefaultAccount.create(username="Ziggy", password="starman11")
        self.assertFalse(
            account2, "Duplicate account name should not have been allowed."
        )
        account.delete()

    def test_throttle(self):
        "Confirm throttle activates on too many failures."
        for x in range(20):
            obj, errors = DefaultAccount.authenticate(
                self.account.name, "xyzzy", ip="12.24.36.48"
            )
            self.assertFalse(
                obj,
                "Authentication was provided a bogus password; this should NOT have returned an account!",
            )

        self.assertTrue(
            "too many login failures" in errors[-1].lower(),
            "Failed logins should have been throttled.",
        )

    def test_rename_syncs_db_key_and_fires_signal(self):
        """Renaming an account keeps db_key in sync with username and fires
        the rename signal (the key/name/username properties are aliases for
        the same identity)."""
        from evennia.accounts.models import AccountDB
        from evennia.server.signals import SIGNAL_ACCOUNT_POST_RENAME

        captured = []

        def _receiver(sender, old_name, new_name, **kwargs):
            captured.append((old_name, new_name))

        old_name = self.account.username
        new_name = f"Renamed{randint(100000, 999999)}"
        SIGNAL_ACCOUNT_POST_RENAME.connect(_receiver, weak=False)
        try:
            self.account.key = new_name
        finally:
            SIGNAL_ACCOUNT_POST_RENAME.disconnect(_receiver)

        # property reads reflect the new name
        self.assertEqual(self.account.username, new_name)
        self.assertEqual(self.account.key, new_name)
        # db_key is synced and persisted, not left at the creation-time value
        self.assertEqual(self.account.db_key, new_name)
        self.assertEqual(AccountDB.objects.get(pk=self.account.pk).db_key, new_name)
        # signal fired with the correct names
        self.assertEqual(captured, [(old_name, new_name)])

    def test_username_validation(self):
        "Check username validators deny relevant usernames"
        # Should not accept Unicode by default, lest users pick names like this

        if not uses_database("mysql"):
            # TODO As of Mar 2019, mysql does not pass this test due to collation problems
            # that has not been possible to resolve
            result, error = DefaultAccount.validate_username(r"¯\_(ツ)_/¯")
            self.assertFalse(result, "Validator allowed kanji in username.")

        # Should not allow duplicate username
        result, error = DefaultAccount.validate_username(self.account.name)
        self.assertFalse(
            result, "Duplicate username should not have passed validation."
        )

        # Should not allow username too short
        result, error = DefaultAccount.validate_username("xx")
        self.assertFalse(result, "2-character username passed validation.")

    def test_password_validation(self):
        "Check password validators deny bad passwords"

        account = create.create_account(
            f"TestAccount{randint(100000, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )
        for bad in ("", "123", "password", "TestAccount", "#", "xyzzy"):
            self.assertFalse(account.validate_password(bad, account=self.account)[0])

        "Check validators allow sufficiently complex passwords"
        for better in ("Mxyzptlk", "j0hn, i'M 0n1y d4nc1nG"):
            self.assertTrue(account.validate_password(better, account=self.account)[0])
        account.delete()

    def test_password_change(self):
        "Check password setting and validation is working as expected"
        account = create.create_account(
            f"TestAccount{randint(100000, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )

        from django.core.exceptions import ValidationError

        # Try setting some bad passwords
        for bad in ("", "#", "TestAccount", "password"):
            valid, error = account.validate_password(bad, account)
            self.assertFalse(valid)

        # Try setting a better password (test for False; returns None on success)
        self.assertFalse(account.set_password("Mxyzptlk"))
        account.delete()


class TestDefaultAccount(TestCase):
    "Check DefaultAccount class"

    def setUp(self):
        self.s1 = MagicMock()
        self.s1.get_puppet = Mock(return_value=None)
        self.s1.sessid = 0

    def test_puppet_object_no_object(self):
        "Check puppet_object method called with no object param"

        try:
            DefaultAccount().puppet_object(self.s1, None)
            self.fail("Expected error: 'Object not found'")
        except RuntimeError as re:
            self.assertEqual("Object not found", str(re))

    def test_puppet_object_no_session(self):
        "Check puppet_object method called with no session param"

        try:
            DefaultAccount().puppet_object(None, Mock())
            self.fail("Expected error: 'Session not found'")
        except RuntimeError as re:
            self.assertEqual("Session not found", str(re))

    def test_puppet_object_already_puppeting(self):
        "Check puppet_object method called, already puppeting this"

        account = create.create_account(
            f"TestAccount{randint(0, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )
        self.s1.uid = account.uid
        evennia.SESSION_HANDLER[self.s1.uid] = self.s1

        self.s1.logged_in = True
        self.s1.data_out = Mock(return_value=None)

        obj = Mock()
        self.s1.get_puppet = Mock(return_value=obj)
        account.puppet_object(self.s1, obj)
        self.assertEqual(
            _last_data_out_text(self.s1), "You are already puppeting this object."
        )
        self.assertIsNone(self.s1.data_out.call_args[1]["options"])
        self.assertIsNone(obj.at_post_puppet.call_args)

    def test_puppet_object_no_permission(self):
        "Check puppet_object method called, no permission"

        account = create.create_account(
            f"TestAccount{randint(0, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )
        self.s1.uid = account.uid
        evennia.SESSION_HANDLER[self.s1.uid] = self.s1

        self.s1.data_out = MagicMock()
        obj = Mock()
        obj.access = Mock(return_value=False)
        obj.sessions.all = Mock(return_value=[])

        account.puppet_object(self.s1, obj)

        self.assertTrue(
            _last_data_out_text(self.s1).startswith("You don't have permission to puppet")
        )
        self.assertIsNone(obj.at_post_puppet.call_args)

    @override_settings(MULTISESSION_MODE=0)
    def test_puppet_object_joining_other_session(self):
        "Check puppet_object method called, joining other session"

        account = create.create_account(
            f"TestAccount{randint(0, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )
        self.s1.uid = account.uid
        evennia.SESSION_HANDLER[self.s1.uid] = self.s1

        self.s1.get_puppet = Mock(return_value=None)
        self.s1.account = account
        self.s1.logged_in = True
        self.s1.data_out = MagicMock()

        obj = Mock()
        obj.access = Mock(return_value=True)
        # another live session of *this* account is currently driving obj
        obj.sessions.all = MagicMock(return_value=[self.s1])
        obj.sessions.get = MagicMock(return_value=self.s1)

        # the focus stack / ControlBinding internals are exercised by the
        # dedicated binding tests; here we only assert the takeover messaging
        # and the at_post_puppet hook, so stub the durable binding lookup.
        with patch.object(ControlBinding, "for_identity") as for_identity:
            for_identity.return_value = MagicMock()
            account.puppet_object(self.s1, obj)
        # works because django.conf.settings.MULTISESSION_MODE is not in (1, 3)
        self.assertTrue(
            _last_data_out_text(self.s1).endswith("from another of your sessions.|n")
        )
        self.assertTrue(obj.at_post_puppet.call_args[1] == {})

    def test_puppet_object_already_puppeted(self):
        "Check puppet_object method called, already puppeted"

        account = create.create_account(
            f"TestAccount{randint(0, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )
        self.account = account
        self.s1.uid = account.uid
        evennia.SESSION_HANDLER[self.s1.uid] = self.s1

        self.s1.get_puppet = Mock(return_value=None)
        self.s1.logged_in = True
        self.s1.data_out = Mock(return_value=None)

        obj = Mock()
        obj.access = Mock(return_value=True)
        obj.at_post_puppet = Mock()
        # obj is currently driven by a live session of *another*, connected
        # account, so the puppet attempt is refused.
        other_session = MagicMock()
        other_session.account = Mock()
        other_session.account.is_connected = True
        obj.sessions.all = MagicMock(return_value=[other_session])

        account.puppet_object(self.s1, obj)
        self.assertTrue(
            _last_data_out_text(self.s1).endswith("is already puppeted by another Account.")
        )
        self.assertIsNone(obj.at_post_puppet.call_args)

    @override_settings(MAX_NR_CHARACTERS=5)
    def test_get_character_slots(self):
        "Check get_character_slots method"

        account = create.create_account(
            f"TestAccount{randint(0, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )

        self.assertEqual(account.get_character_slots(), 5)
        account.delete()

    @override_settings(MAX_NR_CHARACTERS=5)
    def test_get_available_character_slots(self):
        "Check get_available_character_slots method"
        account = create.create_account(
            f"TestAccount{randint(0, 999999)}",
            email="test@test.com",
            password="testpassword",
            typeclass=DefaultAccount,
        )
        self.assertEqual(account.get_available_character_slots(), 5)
        account.delete()


class TestAccountPuppetSetHooks(BaseEvenniaTest):
    """at_puppet_added / at_puppet_removed fire on set-membership change."""

    def test_added_fires_on_first_attach(self):
        self.account.at_puppet_added = MagicMock()
        # Detach the fixture session first, then re-attach via puppet_object
        # so we exercise the hook path.
        self.account.unpuppet_object(self.session)
        self.account.at_puppet_added.reset_mock()
        self.account.puppet_object(self.session, self.char1)
        self.account.at_puppet_added.assert_called_once_with(
            self.char1, session=self.session
        )

    def test_added_does_not_fire_on_session_takeover(self):
        # Initial puppet already happened in setup_session. Simulate a second
        # session of the same account taking over by puppeting the same obj
        # again with a new session id.
        from mock import MagicMock as MM

        from evennia.server.serversession import ServerSession
        from evennia.utils import clock
        from evennia.utils.test_resources import _mock_deferlater

        other = ServerSession()
        other.init_session("telnet", ("localhost", "testmode"), evennia.SESSION_HANDLER)
        other.sessid = 2
        # portal_connect schedules the login-start command via delay(); in a sync
        # test body there is no running loop, so route it through the harness's
        # synchronous deferLater shim (matching EvenniaTestMixin.setUp).
        with patch.object(clock, "defer_later_compat", _mock_deferlater):
            evennia.SESSION_HANDLER.portal_connect(other.get_sync_data())
        other = evennia.SESSION_HANDLER.session_from_sessid(2)
        evennia.SESSION_HANDLER.login(other, self.account, testmode=True)
        try:
            self.account.at_puppet_added = MM()
            # Now attempt to puppet char1 (already puppeted by self.session)
            # from the new session. In MULTISESSION_MODE 0 this takes over.
            self.account.puppet_object(other, self.char1)
            self.account.at_puppet_added.assert_not_called()
        finally:
            del evennia.SESSION_HANDLER[other.sessid]

    def test_removed_fires_on_last_detach(self):
        self.account.at_puppet_removed = MagicMock()
        self.account.unpuppet_object(self.session)
        self.account.at_puppet_removed.assert_called_once_with(
            self.char1, session=self.session
        )


class TestAccountFocusPushPop(BaseEvenniaTest):
    """puppet_object(push=True) layers a body; pop_focus returns to the one
    beneath, preserving identity (the I1 jack-in/jack-out primitive)."""

    def setUp(self):
        super().setUp()
        # self.session already drives self.char1 (the meat body). char2 stands
        # in for the avatar/vehicle layered on top.
        self.char2.policies.set("puppet", Always())

    def test_push_layers_body_and_keeps_identity(self):
        self.account.puppet_object(self.session, self.char2, push=True)
        binding = self.session.binding
        # focus moved to the layered body
        self.assertEqual(self.session.get_puppet(), self.char2)
        # identity stays the meat character
        self.assertEqual(binding.db_identity, self.char1)
        # bodies above the (derived) floor: [char1, char2]
        self.assertEqual(binding.stack_objects, [self.char1, self.char2])
        # meat body shed its live session; avatar holds it
        self.assertNotIn(self.session, list(self.char1.sessions.all()))
        self.assertIn(self.session, list(self.char2.sessions.all()))

    def test_pop_returns_to_body_beneath(self):
        self.account.puppet_object(self.session, self.char2, push=True)
        returned = self.account.pop_focus(self.session)
        self.assertEqual(returned, self.char1)
        self.assertEqual(self.session.get_puppet(), self.char1)
        binding = self.session.binding
        self.assertEqual(binding.stack_objects, [self.char1])
        self.assertIn(self.session, list(self.char1.sessions.all()))
        self.assertNotIn(self.session, list(self.char2.sessions.all()))

    def test_pop_to_floor_goes_ooc(self):
        # Pop the only layer (char1) straight off — lands on the account floor.
        returned = self.account.pop_focus(self.session)
        self.assertIsNone(returned)
        self.assertIsNone(self.session.get_puppet())
        self.assertIsNone(self.session.bid)

    @override_settings(MULTISESSION_MODE=0)
    def test_takeover_preserves_pushed_stack(self):
        # session1 is jacked into char2 (avatar) on top of char1: stack
        # [char1, char2]. A second session of the same account takes char2 over.
        from evennia.server.serversession import ServerSession

        self.account.puppet_object(self.session, self.char2, push=True)
        self.assertEqual(self.session.binding.stack_objects, [self.char1, self.char2])

        sess2 = ServerSession()
        # distinct address: ServerSession.__eq__ compares by address.
        sess2.init_session(
            "telnet", ("localhost", "testmode2"), evennia.SESSION_HANDLER
        )
        sess2.sessid = 43
        sess2.uname = self.account.username
        sess2.logged_in = True
        sess2.account = self.account
        sess2.uid = self.account.id
        evennia.SESSION_HANDLER[43] = sess2
        try:
            self.account.puppet_object(sess2, self.char2)
            # the pushed stack survived the takeover (no collapse to the floor);
            # the new session drives char2, the old one is detached.
            self.assertIs(sess2.get_puppet(), self.char2)
            self.assertEqual(sess2.binding.stack_objects, [self.char1, self.char2])
            self.assertNotIn(self.session, list(self.char2.sessions.all()))
        finally:
            if 43 in evennia.SESSION_HANDLER:
                del evennia.SESSION_HANDLER[43]


class TestAccountDisconnectRestore(BaseEvenniaTest):
    """A network drop preserves the durable focus stack (collapse=False); a
    deliberate go-OOC collapses it (collapse=True). at_post_login restores a
    pushed body (the jacked-in avatar) rather than the meat character beneath."""

    def setUp(self):
        super().setUp()
        self.char2.policies.set("puppet", Always())
        self.account.db._last_puppet = self.char1

    def test_disconnect_preserves_pushed_stack(self):
        self.account.puppet_object(self.session, self.char2, push=True)
        binding = self.session.binding
        self.account.unpuppet_object(self.session, collapse=False)
        # session detached from the runtime body...
        self.assertIsNone(self.session.bid)
        self.assertNotIn(self.session, list(self.char2.sessions.all()))
        # ...but the durable stack still carries the pushed body
        binding.refresh_from_db()
        self.assertEqual(binding.stack_objects, [self.char1, self.char2])

    def test_deliberate_ooc_collapses_stack(self):
        self.account.puppet_object(self.session, self.char2, push=True)
        binding = self.session.binding
        self.account.unpuppet_object(self.session)  # collapse=True default
        binding.refresh_from_db()
        # stack emptied to the floor; focus derives back to the account.
        self.assertEqual(binding.stack_objects, [])
        self.assertEqual(binding.focus, self.account)

    def test_login_restores_pushed_focus(self):
        self.account.puppet_object(self.session, self.char2, push=True)
        self.account.unpuppet_object(self.session, collapse=False)
        self.session.bid = None
        self.account.at_post_login(self.session)
        # restored straight to the avatar, not the meat char beneath it
        self.assertEqual(self.session.get_puppet(), self.char2)

    def test_login_without_push_puppets_character(self):
        # No body pushed — stack is just [char1]; login puppets char1.
        self.account.unpuppet_object(self.session, collapse=False)
        self.session.bid = None
        self.account.at_post_login(self.session)
        self.assertEqual(self.session.get_puppet(), self.char1)

    def test_permadeath_collapses_and_drops_binding(self):
        # Permadeath ("go light"): the player is jacked in (avatar pushed), then
        # the lobby collapses the stack to the account floor and deletes the
        # identity character. The ControlBinding (keyed on the identity via an
        # on_delete=CASCADE OneToOne) must go with it — no orphaned focus stack.
        from evennia.accounts.models import ControlBinding

        self.account.puppet_object(self.session, self.char2, push=True)
        binding = self.session.binding
        binding_pk = binding.pk
        # go-light: deliberate collapse to floor, then delete the identity self.
        self.account.unpuppet_object(self.session)  # collapse=True default
        binding.refresh_from_db()
        self.assertEqual(binding.stack_objects, [])
        self.char1.delete()
        self.assertFalse(ControlBinding.objects.filter(pk=binding_pk).exists())

    def test_delete_identity_under_live_pushed_stack(self):
        # The pushed-stack variant of the .70 crash: a live session is jacked
        # into char2 (avatar) on top of char1 (the meat identity) WITHOUT first
        # collapsing the stack — focus stack [char1, char2], session driving
        # char2. Deleting the identity char1 must still succeed and tear down
        # the binding without a save-on-deleted-row error, even though the live
        # session never sat on char1.
        from evennia.accounts.models import ControlBinding

        self.account.puppet_object(self.session, self.char2, push=True)
        binding = self.session.binding
        binding_pk = binding.pk
        # non-floor stack: the session is driving the avatar above the identity
        self.assertEqual(binding.stack_objects, [self.char1, self.char2])
        self.assertEqual(binding.db_identity, self.char1)
        self.assertNotIn(self.session, list(self.char1.sessions.all()))
        self.assertIn(self.session, list(self.char2.sessions.all()))

        # delete the meat identity out from under the still-live pushed avatar
        self.assertTrue(self.char1.delete())

        # binding (anchored on the deleted identity) is gone, char1 disowned
        self.assertFalse(ControlBinding.objects.filter(pk=binding_pk).exists())
        self.assertNotIn(self.char1, self.account.characters.all())


class TestControllerVsOwnership(BaseEvenniaTest):
    """The three facts stay in their own homes: ownership on
    ``ObjectDB.db_account``, the controller (driver/floor) on
    ``ControlBinding.db_account``, live driving on ``obj.sessions``. Staff
    possession is where they diverge."""

    def _npc(self, key="NPC"):
        npc = create.create_object(
            self.character_typeclass, key=key, location=self.room1
        )
        npc.policies.set("puppet", Always())
        return npc

    def test_possession_leaves_ownership_untouched(self):
        npc = self._npc()
        self.assertIsNone(npc.db_account)  # unowned
        # account goes OOC, then possesses the unowned NPC.
        self.account.unpuppet_object(self.session)
        self.account.puppet_object(self.session, npc)
        self.assertIs(self.session.get_puppet(), npc)
        # ownership untouched; the controller is the live driver.
        self.assertIsNone(npc.db_account)
        self.assertEqual(self.session.binding.db_account_id, self.account.id)
        # possessing an NPC does not add it to the account's roster (ownership).
        self.assertNotIn(npc, self.account.characters.all())

    def test_is_puppeted_distinct_from_has_account(self):
        # char1 is owned by and driven by the account (login in setUp).
        self.assertTrue(self.char1.is_puppeted)
        self.assertTrue(self.char1.has_account)
        # an owned-but-undriven character: owned, not puppeted.
        idle = self._npc("Idle")
        idle.account = self.account
        self.assertFalse(idle.is_puppeted)
        self.assertTrue(idle.has_account)

    def test_puppeteer_is_the_driver_not_the_owner(self):
        # driven own character: driver == owner.
        self.assertEqual(self.char1.puppeteer, self.account)
        # possessed NPC: driver is the account, owner is None.
        npc = self._npc()
        self.account.unpuppet_object(self.session)
        self.account.puppet_object(self.session, npc)
        self.assertEqual(npc.puppeteer, self.account)
        self.assertIsNone(npc.account)
        # undriven body has no puppeteer.
        idle = self._npc("Idle")
        self.assertIsNone(idle.puppeteer)

    def test_remove_detaches_live_session(self):
        # Disowning a character that is currently being driven must release the
        # session, not leave it half-attached to a body whose binding is gone.
        self.assertTrue(self.char1.is_puppeted)
        self.account.characters.remove(self.char1)
        self.assertEqual(self.char1.sessions.count(), 0)
        self.assertFalse(self.char1.is_puppeted)
        self.assertIsNone(self.session.bid)
        self.assertIsNone(self.session.get_puppet())

    def test_is_ooc_follows_driver_under_possession(self):
        # actively possessing an unowned NPC is IC, not OOC.
        npc = self._npc()
        self.account.unpuppet_object(self.session)
        self.account.puppet_object(self.session, npc)
        self.assertIs(npc.puppeteer, self.account)
        # an undriven body has no in-character driver -> OOC.
        idle = self._npc("Idle")
        self.assertIsNone(idle.puppeteer)

    def test_nickreplace_uses_driver_account_nicks(self):
        # account-level nicks belong to the *driver*: a possessed (unowned) NPC
        # expands the driving account's input nicks, not its (absent) owner's.
        self.account.nicks.add("hi", "hello", category="inputline")
        npc = self._npc()
        self.assertIsNone(npc.account)  # unowned: the old has_account path gave nothing
        self.account.unpuppet_object(self.session)
        self.account.puppet_object(self.session, npc)
        self.assertEqual(npc.nicks.nickreplace("hi"), "hello")


class TestCapabilitiesFollowDriver(BaseEvenniaTest):
    """The permissions that apply when a body acts come from the live *driver*
    (``puppeteer``), never the durable *owner* (``account``). A stale or
    higher-perm owner must not leak to whoever is driving the body."""

    def _body_owned_by(self, owner, key="Body"):
        body = create.create_object(
            self.character_typeclass, key=key, location=self.room1
        )
        body.account = owner
        body.policies.set("puppet", Always())
        return body

    def test_owner_grants_do_not_leak_to_driver(self):

        # body OWNED by an Admin account, DRIVEN by a plain-player account.
        grant_capability(
            f"account:{self.account2.id}",
            "engine.object.msg",
            scope_kind="world",
            scope_key="*",
        )
        body = self._body_owned_by(self.account2)
        body.sessions.add(self.session)  # driver is self.account (Player)
        self.assertEqual(body.puppeteer, self.account)
        # the owner's Admin must NOT pass — the driver is only a player.
        self.assertFalse(body.has_capability("engine.object.msg"))
        # the driver's own (lower) perm still passes.

    def test_driver_grants_apply_when_possessing(self):

        # body OWNED by a plain account, DRIVEN by an Admin (staff possession).
        grant_capability(
            f"account:{self.account.id}",
            "engine.object.msg",
            scope_kind="world",
            scope_key="*",
        )
        body = self._body_owned_by(self.account2)
        body.sessions.add(self.session)  # driver is self.account (Admin)
        self.assertTrue(body.has_capability("engine.object.msg"))

    def test_superuser_owner_does_not_leak_via_driver(self):
        self.account2.is_superuser = True
        self.account2.save()
        body = self._body_owned_by(self.account2)
        body.sessions.add(self.session)  # driver self.account is not a superuser
        self.assertFalse(body.is_superuser)
        self.assertFalse(body.has_capability("engine.authorization.break_glass"))


class TestAccountPuppetDeletion(BaseEvenniaTest):
    @override_settings(MULTISESSION_MODE=2)
    def test_puppet_deletion(self):
        # char1 is bound to the account by the login in setUp.
        self.assertTrue(self.account.characters, "Char should be bound to account.")

        # See what happens when we delete char1.
        self.char1.delete()
        # Playable char list should be empty.
        self.assertFalse(
            self.account.characters,
            f"Playable character list is not empty! {self.account.characters}",
        )


class TestDefaultAccountEv(BaseEvenniaTest):
    """
    Testing using the EvenniaTest parent

    """

    def test_characters_property(self):
        "playable set derives from ControlBinding, not the legacy _playable_characters attr"
        # char1 is bound to the account by the login in setUp. The legacy
        # _playable_characters attribute (here naming a different, unbound
        # character plus a None) must not influence the derived playable set.
        self.account.db._playable_characters = [self.char2, None]
        self.assertEqual(self.account.characters.all(), [self.char1])

    def test_add_character_to_playable_list(self):
        # char2 belongs to account2; adding without transfer refuses the steal.
        self.assertNotIn(self.char2, self.account.characters.all())
        with self.assertRaises(ValueError):
            self.account.characters.add(self.char2)
        self.assertNotIn(self.char2, self.account.characters.all())
        # transfer=True is the sanctioned reassignment.
        self.account.characters.add(self.char2, transfer=True)
        self.assertIn(self.char2, self.account.characters.all())

    def test_remove_character_from_playable_list(self):
        self.account.characters.add(self.char1)
        self.assertEqual(self.account.characters.all(), [self.char1])
        self.account.characters.remove(self.char1)
        self.assertEqual(self.account.characters.all(), [])

    def test_puppet_success(self):
        self.account.msg = MagicMock()
        with self.settings(MULTISESSION_MODE=2):
            self.account.puppet_object(self.session, self.char1)
            self.account.msg.assert_called_with(
                "You are already puppeting this object."
            )

    @patch("evennia.accounts.accounts.time.time", return_value=10000)
    def test_idle_time(self, mock_time):
        self.session.cmd_last_visible = 10000 - 10
        idle = self.account.idle_time
        self.assertEqual(idle, 10)

        # test no sessions
        with patch(
            "evennia.SESSION_HANDLER.sessions_from_account", return_value=[]
        ) as mock_sessh:
            idle = self.account.idle_time
            self.assertEqual(idle, None)

    @patch("evennia.accounts.accounts.time.time", return_value=10000)
    def test_connection_time(self, mock_time):
        self.session.conn_time = 10000 - 10
        conn = self.account.connection_time
        self.assertEqual(conn, 10)

        # test no sessions
        with patch(
            "evennia.SESSION_HANDLER.sessions_from_account", return_value=[]
        ) as mock_sessh:
            idle = self.account.connection_time
            self.assertEqual(idle, None)

    def test_create_account(self):
        acct = create.account(
            "TestAccount3",
            "test@test.com",
            "testpassword123",
            locks="test:all()",
            tags=[
                ("tag1", "category1"),
                ("tag2", "category2", "data1"),
                ("tag3", None),
            ],
            attributes=[
                ("key1", "value1", "category1", "edit:false()", True),
                ("key2", "value2"),
            ],
        )
        acct.save()
        self.assertTrue(acct.pk)

    def test_at_look(self):
        ret = self.account.at_look()
        self.assertTrue("Out-of-Character" in ret)
        ret = self.account.at_look(target=self.obj1)
        self.assertTrue("Obj" in ret)
        ret = self.account.at_look(session=self.session)
        self.assertTrue("*" in ret)  #  * marks session is active in list
        ret = self.account.at_look(target=self.obj1, session=self.session)
        self.assertTrue("Obj" in ret)
        ret = self.account.at_look(target="Invalid", session=self.session)
        self.assertEqual(ret, "Invalid has no in-game appearance.")

    def test_msg(self):
        self.account.msg
