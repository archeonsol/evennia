"""Credential hashing must leave the game loop available for other players."""

import asyncio
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from evennia.utils import clock
from evennia.utils.create import create_account
from evennia.utils.test_resources import EvenniaTestCase
from evennia.web.utils import backends


class TestAsyncCredentials(EvenniaTestCase):
    """Exercise real accounts while only password strings cross to a worker."""

    def setUp(self):
        """Create a current-hash account in the owner transaction."""
        self.account = create_account(
            "async-login",
            email="",
            password="correct-password",
            typeclass="evennia.accounts.accounts.DefaultAccount",
        )
        self.backend = backends.CaseInsensitiveModelBackend()

    def _run(self, operation):
        """Keep the owner transaction visible inside the same-thread test loop."""
        owner_connection = connections["default"]

        async def exercise():
            """Bind the fixture connection only for owner-side work."""
            previous_loop = clock.get_bound_loop()
            previous_thread = clock.get_loop_thread_id()
            clock.bind_loop(asyncio.get_running_loop())
            connections["default"] = owner_connection
            try:
                return await operation
            finally:
                del connections["default"]
                clock._main_loop = previous_loop
                clock._loop_thread_id = previous_thread

        return asyncio.run(exercise())

    def test_account_api_uses_the_async_backend(self):
        """The public Account API authenticates through Django's configured backend."""
        from evennia.accounts.accounts import DefaultAccount

        account, errors = self._run(DefaultAccount.aauthenticate("async-login", "correct-password"))
        self.assertEqual(account.pk, self.account.pk)
        self.assertEqual(errors, [])

    def test_custom_sync_backend_policy_cannot_be_bypassed(self):
        """A backend subclass must carry its custom policy into async authentication."""

        class CustomBackend(backends.CaseInsensitiveModelBackend):
            """Deny authentication through a custom synchronous backend policy."""

            def authenticate(self, *args, **kwargs):
                """Refuse this account under the custom policy."""
                return None

        with self.assertRaises(ImproperlyConfigured):
            self._run(
                CustomBackend().aauthenticate(
                    None, username="async-login", password="correct-password"
                )
            )

    def test_password_check_does_not_block_other_loop_work(self):
        """An unrelated callback runs before a held password check finishes."""
        started = threading.Event()
        release = threading.Event()
        worker_threads = []

        def verify(raw, encoded):
            """Hold only the hasher, without accessing the database in its worker."""
            worker_threads.append(threading.get_ident())
            started.set()
            if not release.wait(5):
                raise AssertionError("Game loop could not release the password worker")
            return True, None

        async def exercise():
            """Release the worker from unrelated game-loop work."""
            task = asyncio.create_task(
                self.backend.aauthenticate(
                    None, username="ASYNC-LOGIN", password="correct-password"
                )
            )
            try:
                async with asyncio.timeout(3):
                    while not started.is_set() and not task.done():
                        await asyncio.sleep(0.001)
                self.assertTrue(started.is_set())
                self.assertFalse(task.done())
                release.set()
                result = await task
                self.assertEqual(result.pk, self.account.pk)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

        with patch.object(backends, "_verify_password", side_effect=verify, create=True):
            self._run(exercise())
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], threading.get_ident())

    def test_wrong_password_is_rejected(self):
        """A worker verdict cannot bypass password validation."""
        result = self._run(
            self.backend.aauthenticate(None, username="async-login", password="wrong")
        )
        self.assertIsNone(result)

    def test_password_change_during_verification_rejects_login(self):
        """A successful check cannot authorize a superseded password."""

        async def changed_password(*args):
            """Simulate an owner-side password reset while verification yields."""
            get_user_model().objects.filter(pk=self.account.pk).update(password="changed")
            return True, None

        with patch.object(backends.defer, "in_thread", side_effect=changed_password):
            result = self._run(
                self.backend.aauthenticate(
                    None, username="async-login", password="correct-password"
                )
            )
        self.assertIsNone(result)

    def test_deactivation_during_verification_rejects_login(self):
        """Account eligibility is checked again after the worker returns."""

        async def deactivate(*args):
            """Deactivate the account before delivering a successful hash verdict."""
            get_user_model().objects.filter(pk=self.account.pk).update(is_active=False)
            return True, None

        with patch.object(backends.defer, "in_thread", side_effect=deactivate):
            result = self._run(
                self.backend.aauthenticate(
                    None, username="async-login", password="correct-password"
                )
            )
        self.assertIsNone(result)

    def test_legacy_hash_is_upgraded_off_loop(self):
        """Hash upgrades preserve the password and never run the hasher on the owner."""
        legacy = make_password("correct-password", hasher="pbkdf2_sha1")
        get_user_model().objects.filter(pk=self.account.pk).update(password=legacy)
        threads = []
        original = backends._verify_password

        def verify(*args):
            """Record the thread performing verification and any hash upgrade."""
            threads.append(threading.get_ident())
            return original(*args)

        with patch.object(backends, "_verify_password", side_effect=verify):
            result = self._run(
                self.backend.aauthenticate(
                    None, username="async-login", password="correct-password"
                )
            )
        self.assertEqual(result.pk, self.account.pk)
        stored = (
            get_user_model()
            .objects.filter(pk=self.account.pk)
            .values_list("password", flat=True)
            .get()
        )
        self.assertFalse(stored.startswith("pbkdf2_sha1$"))
        self.assertTrue(check_password("correct-password", stored))
        self.assertTrue(all(thread != threading.get_ident() for thread in threads))
