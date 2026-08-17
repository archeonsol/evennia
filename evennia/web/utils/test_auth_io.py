"""Tests for owner-safe stock web authentication and password services."""

import asyncio
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import Mock, patch

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.tokens import default_token_generator
from django.db import close_old_connections
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from evennia.accounts.models import AccountDB
from evennia.utils import clock
from evennia.utils.create import create_account
from evennia.utils.test_resources import BaseEvenniaTest
from evennia.web.utils.auth import (
    AuthenticationRequest,
    PasswordMutationRequest,
    RegistrationRequest,
    RegistrationResult,
    authenticate_account,
    mutate_password,
    owner_update_last_login,
    register_account,
)
from evennia.web.utils.middleware import SharedLoginMiddleware


class AuthRequestSafetyTest(SimpleTestCase):
    """Credential records never expose secrets through repr."""

    def test_all_secret_fields_are_repr_hidden(self):
        auth_request = AuthenticationRequest("name", "password")
        password_request = PasswordMutationRequest(
            1,
            "change",
            new_password="new-secret",
            old_password="old-secret",
            reset_token="token-secret",
        )
        for secret in ("password", "new-secret", "old-secret", "token-secret"):
            self.assertNotIn(secret, repr((auth_request, password_request)))
        hidden = {item.name for item in fields(PasswordMutationRequest) if not item.repr}
        self.assertEqual(hidden, {"new_password", "old_password", "reset_token"})

    def test_shared_login_rechecks_a_stale_website_stamp(self):
        class Session(dict):
            session_key = "existing"

            def save(self):
                raise AssertionError("An existing session must not be resaved")

        account = SimpleNamespace(pk=7)
        request = SimpleNamespace(
            user=AnonymousUser(),
            session=Session(
                website_authenticated_uid=7,
                webclient_authenticated_uid=7,
            ),
        )
        with (
            patch("evennia.web.utils.middleware.authenticate", return_value=account) as auth,
            patch("evennia.web.utils.middleware.login") as login,
        ):
            SharedLoginMiddleware.make_shared_login(request)
        auth.assert_called_once_with(request=request, autologin_id=7)
        login.assert_called_once_with(request, account)
        self.assertEqual(request.session["website_authenticated_uid"], 7)


class OwnerAuthenticationServiceTest(BaseEvenniaTest):
    """Authentication and password changes remain on canonical owner state."""

    def setUp(self):
        super().setUp()
        self.account.set_password("Old-password-483!")
        self.account.save(update_fields=["password"])

    def test_authentication_checks_password_and_returns_only_id(self):
        accepted = authenticate_account(
            AuthenticationRequest(self.account.username, "Old-password-483!")
        )
        rejected = authenticate_account(
            AuthenticationRequest(self.account.username, "wrong-password")
        )
        self.assertEqual(accepted.status, "authenticated")
        self.assertEqual(accepted.account_id, self.account.pk)
        self.assertEqual(rejected.status, "rejected")

    def test_password_change_rechecks_old_password_on_owner(self):
        rejected = mutate_password(
            PasswordMutationRequest(
                self.account.pk,
                "change",
                new_password="New-password-927!",
                old_password="wrong-password",
            )
        )
        self.assertEqual(rejected.status, "rejected")

        changed = mutate_password(
            PasswordMutationRequest(
                self.account.pk,
                "change",
                new_password="New-password-927!",
                old_password="Old-password-483!",
            )
        )
        self.assertEqual(changed.status, "changed")
        self.account.refresh_from_db()
        self.assertTrue(self.account.check_password("New-password-927!"))

    def test_last_login_receiver_swallows_auxiliary_bridge_failure(self):
        with patch(
            "evennia.web.utils.auth.run_on_io_thread",
            side_effect=RuntimeError("bridge unavailable"),
        ):
            owner_update_last_login(AccountDB, self.account)

    def test_reset_token_is_rechecked_on_owner(self):
        token = default_token_generator.make_token(self.account)
        self.account.email = "token-invalidated@example.com"
        self.account.save(update_fields=["email"])

        result = mutate_password(
            PasswordMutationRequest(
                self.account.pk,
                "reset",
                new_password="Reset-password-159!",
                reset_token=token,
            )
        )

        self.assertEqual(result.status, "rejected")
        self.assertTrue(self.account.check_password("Old-password-483!"))

    def test_admin_can_preserve_unusable_password_contract(self):
        self.account.is_staff = True
        self.account.is_superuser = True
        self.account.save(update_fields=["is_staff", "is_superuser"])

        result = mutate_password(
            PasswordMutationRequest(
                self.account.pk,
                "admin_unusable",
                actor_id=self.account.pk,
            )
        )

        self.assertEqual(result.status, "changed")
        self.account.refresh_from_db()
        self.assertFalse(self.account.has_usable_password())

    def test_replacement_receiver_is_unique_for_account_sender(self):
        matching = [
            receiver
            for receiver in user_logged_in._live_receivers(AccountDB)[0]
            if receiver is owner_update_last_login
        ]
        self.assertEqual(len(matching), 1)

    def test_fully_compensated_internal_registration_fault_stays_fault(self):
        outcome = SimpleNamespace(
            disposition="fault",
            account=None,
            accounts=(),
            objects=(),
            account_ids=(),
            object_ids=(),
            issues=(),
        )
        account_typeclass = SimpleNamespace(create_with_provenance=Mock(return_value=outcome))
        with patch(
            "evennia.web.utils.auth.class_from_module",
            return_value=account_typeclass,
        ):
            result = register_account(
                RegistrationRequest(
                    "faulted-registration",
                    "fault@example.com",
                    "Fault-password-741!",
                    typeclass_path="tests.FaultAccount",
                )
            )
        self.assertEqual(result.status, "fault")


@override_settings(ROOT_URLCONF="evennia.web.urls")
class OwnerAuthenticationASGITest(TransactionTestCase):
    """Real ASGI auth requests retain Django sessions around owner mutations."""

    def setUp(self):
        super().setUp()
        self.account = create_account(
            "asgiauthaccount",
            "asgi-auth@example.com",
            password="Old-password-741!",
        )

    def tearDown(self):
        close_old_connections()
        AccountDB.flush_instance_cache(force=True)
        super().tearDown()

    async def _with_bound_loop(self, operation):
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        clock.bind_loop(loop)
        try:
            return await operation()
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()

    async def test_login_and_last_login_update_on_owner(self):
        client = AsyncClient()

        async def operation():
            return await client.post(
                reverse("login"),
                {
                    "username": self.account.username,
                    "password": "Old-password-741!",
                },
            )

        response = await self._with_bound_loop(operation)
        self.assertEqual(response.status_code, 302, response.content.decode())
        account = AccountDB.objects.get(pk=self.account.pk)
        self.assertIsNotNone(account.last_login)
        self.assertEqual(int(client.session["_auth_user_id"]), self.account.pk)

    async def test_password_change_updates_current_session_hash(self):
        client = AsyncClient()
        await client.aforce_login(self.account)

        async def operation():
            return await client.post(
                reverse("password_change"),
                {
                    "old_password": "Old-password-741!",
                    "new_password1": "New-password-852!",
                    "new_password2": "New-password-852!",
                },
            )

        response = await self._with_bound_loop(operation)
        self.assertEqual(response.status_code, 302, response.content.decode())
        account = AccountDB.objects.get(pk=self.account.pk)
        self.assertTrue(account.check_password("New-password-852!"))
        self.assertIn("_auth_user_hash", client.session)

    async def test_registration_returns_plain_success_without_worker_save(self):
        client = AsyncClient()

        async def operation():
            return await client.post(
                reverse("register"),
                {
                    "username": "asgiregistered",
                    "email": "registered@example.com",
                    "password1": "Registered-password-963!",
                    "password2": "Registered-password-963!",
                },
            )

        response = await self._with_bound_loop(operation)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertTrue(AccountDB.objects.filter(username="asgiregistered").exists())

    async def test_registration_recovery_required_is_nonretryable(self):
        client = AsyncClient()

        async def operation():
            with patch(
                "evennia.web.website.views.accounts.run_on_io_thread",
                return_value=RegistrationResult(
                    "recovery_required",
                    recovery_ids=(41, 42),
                ),
            ):
                return await client.post(
                    reverse("register"),
                    {
                        "username": "asgirecovery",
                        "email": "recovery@example.com",
                        "password1": "Recovery-password-185!",
                        "password2": "Recovery-password-185!",
                    },
                )

        response = await self._with_bound_loop(operation)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["X-Evennia-Retryable"], "false")

    async def test_registration_internal_fault_is_not_validation_rejection(self):
        client = AsyncClient()

        async def operation():
            with patch(
                "evennia.web.website.views.accounts.run_on_io_thread",
                return_value=RegistrationResult("fault"),
            ):
                return await client.post(
                    reverse("register"),
                    {
                        "username": "asgiregistrationfault",
                        "email": "fault@example.com",
                        "password1": "Fault-password-296!",
                        "password2": "Fault-password-296!",
                    },
                )

        response = await self._with_bound_loop(operation)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["X-Evennia-Retryable"], "false")
