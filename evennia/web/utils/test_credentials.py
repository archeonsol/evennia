"""Tests for worker-side credential checking.

This is the authentication path the player website shares, so the central
assertion is not that the new path works but that it and the owner path
**never disagree**. A divergence here is either a lockout or a hole.

The second assertion is the reason the change exists: signing in must work
while the game server is down. A console that keeps serving reads during an
outage is worthless if the operator arriving at that outage cannot get in.

"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from evennia.web.utils import backends
from evennia.web.utils.auth import (
    AuthenticationRequest,
    authenticate_account,
    check_credentials,
    rehash_password,
)
from evennia.web.utils.io import IOThreadCallUnavailable

PASSWORD = "Correct-horse-battery-7!"

#: A registered but non-preferred hasher, so a password stored under it
#: verifies and asks to be upgraded. Read from settings rather than named
#: outright: the project's hasher list is free to change.
LEGACY_ALGORITHM = "pbkdf2_sha1"


def _legacy_hash(raw):
    """Return one password hashed under a superseded hasher."""
    from django.contrib.auth.hashers import make_password

    return make_password(raw, hasher=LEGACY_ALGORITHM)


class CredentialTestCase(TestCase):
    """One active account with a known password."""

    def setUp(self):
        self.account = get_user_model().objects.create(username="operator", is_active=True)
        self.account.set_password(PASSWORD)
        self.account.save()


class TestPathsAgree(CredentialTestCase):
    """The worker path and the owner path return identical verdicts."""

    def _both(self, request):
        """Return ``(worker, owner)`` results for one request."""
        return check_credentials(request), authenticate_account(request)

    def _assert_agree(self, request, label):
        worker, owner = self._both(request)
        self.assertEqual(worker.status, owner.status, f"{label}: status differs")
        self.assertEqual(worker.account_id, owner.account_id, f"{label}: account differs")

    def test_correct_password(self):
        self._assert_agree(AuthenticationRequest("operator", PASSWORD), "correct password")

    def test_wrong_password(self):
        self._assert_agree(AuthenticationRequest("operator", "wrong"), "wrong password")

    def test_unknown_username(self):
        self._assert_agree(AuthenticationRequest("nobody", PASSWORD), "unknown username")

    def test_case_insensitive_username(self):
        self._assert_agree(AuthenticationRequest("OPERATOR", PASSWORD), "mixed-case username")

    def test_inactive_account(self):
        self.account.is_active = False
        self.account.save()
        self._assert_agree(AuthenticationRequest("operator", PASSWORD), "inactive account")

    def test_unusable_password(self):
        self.account.set_unusable_password()
        self.account.save()
        self._assert_agree(AuthenticationRequest("operator", "anything"), "unusable password")
        self._assert_agree(AuthenticationRequest("operator", "!"), "unusable password literal")

    def test_empty_credentials(self):
        self._assert_agree(AuthenticationRequest("", ""), "empty username")
        self._assert_agree(AuthenticationRequest("operator", None), "no password")

    def test_autologin(self):
        self._assert_agree(AuthenticationRequest(autologin_id=self.account.pk), "autologin")

    def test_autologin_for_a_missing_account(self):
        self._assert_agree(AuthenticationRequest(autologin_id=999999), "autologin missing")

    def test_autologin_for_an_inactive_account(self):
        self.account.is_active = False
        self.account.save()
        self._assert_agree(
            AuthenticationRequest(autologin_id=self.account.pk), "autologin inactive"
        )


class TestWorkerPathAccepts(CredentialTestCase):
    """The worker path accepts and rejects on its own terms."""

    def test_accepts_a_valid_password(self):
        result = check_credentials(AuthenticationRequest("operator", PASSWORD))
        self.assertEqual(result.status, "authenticated")
        self.assertEqual(result.account_id, self.account.pk)

    def test_rejects_a_wrong_password(self):
        self.assertEqual(
            check_credentials(AuthenticationRequest("operator", "nope")).status, "rejected"
        )

    def test_builds_no_model_instance(self):
        # The whole reason the check was owner-scoped: materializing an
        # AccountDB runs identity-cached construction. values() does not.
        from evennia.accounts.models import AccountDB

        with patch.object(AccountDB, "__init__", side_effect=AssertionError("instance built")):
            result = check_credentials(AuthenticationRequest("operator", PASSWORD))
        self.assertEqual(result.status, "authenticated")


class TestDegradedLogin(CredentialTestCase):
    """Signing in works while the game server is down."""

    def test_authentication_never_crosses_the_bridge(self):
        with patch.object(
            backends, "run_on_io_thread", side_effect=IOThreadCallUnavailable("server is down")
        ):
            account = backends.CaseInsensitiveModelBackend().authenticate(
                None, username="operator", password=PASSWORD
            )
        self.assertIsNotNone(account, "an outage must not prevent signing in")
        self.assertEqual(account.pk, self.account.pk)

    def test_a_wrong_password_is_still_refused_during_an_outage(self):
        with patch.object(
            backends, "run_on_io_thread", side_effect=IOThreadCallUnavailable("server is down")
        ):
            account = backends.CaseInsensitiveModelBackend().authenticate(
                None, username="operator", password="wrong"
            )
        self.assertIsNone(account)

    def test_an_inactive_account_is_still_refused_during_an_outage(self):
        self.account.is_active = False
        self.account.save()
        with patch.object(
            backends, "run_on_io_thread", side_effect=IOThreadCallUnavailable("server is down")
        ):
            account = backends.CaseInsensitiveModelBackend().authenticate(
                None, username="operator", password=PASSWORD
            )
        self.assertIsNone(account)


class TestRehash(CredentialTestCase):
    """Hash upgrades are opportunistic and never block a sign-in."""

    def test_a_current_hash_needs_no_rehash(self):
        result = check_credentials(AuthenticationRequest("operator", PASSWORD))
        self.assertFalse(result.needs_rehash)

    def test_a_superseded_hash_is_flagged(self):
        # pbkdf2_sha1 is registered but not preferred, so a password stored
        # under it verifies and wants an upgrade to the first hasher.
        get_user_model().objects.filter(pk=self.account.pk).update(password=_legacy_hash(PASSWORD))
        result = check_credentials(AuthenticationRequest("operator", PASSWORD))
        self.assertEqual(result.status, "authenticated")
        self.assertTrue(result.needs_rehash)

    def test_rehash_rewrites_the_stored_hash(self):
        get_user_model().objects.filter(pk=self.account.pk).update(password=_legacy_hash(PASSWORD))
        self.assertTrue(rehash_password(self.account.pk, PASSWORD))
        stored = get_user_model().objects.filter(pk=self.account.pk).values("password").first()
        self.assertFalse(stored["password"].startswith(LEGACY_ALGORITHM))

    def test_rehash_refuses_a_wrong_password(self):
        self.assertFalse(rehash_password(self.account.pk, "wrong"))

    def test_rehash_refuses_a_missing_account(self):
        self.assertFalse(rehash_password(999999, PASSWORD))

    def test_a_failed_upgrade_does_not_fail_the_sign_in(self):
        get_user_model().objects.filter(pk=self.account.pk).update(password=_legacy_hash(PASSWORD))
        with patch.object(
            backends, "run_on_io_thread", side_effect=IOThreadCallUnavailable("server is down")
        ):
            account = backends.CaseInsensitiveModelBackend().authenticate(
                None, username="operator", password=PASSWORD
            )
        self.assertIsNotNone(account, "a deferred hash upgrade must not block a sign-in")
