"""IO-boundary tests for the stock object admin."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.test import RequestFactory, SimpleTestCase

from evennia.objects.models import ObjectDB
from evennia.utils.test_resources import BaseEvenniaTest
from evennia.web.admin import objects as object_admin
from evennia.web.utils.io import IOThreadCallIndeterminate, IOThreadCallTimeout


class ObjectAdminLinkServiceTest(BaseEvenniaTest):
    """Verify the admin link service completes all game-state work together."""

    def test_link_service_mutates_and_returns_plain_result(self):
        """The service links the object without returning either live model."""
        self.account.is_staff = True
        self.account.save(update_fields=["is_staff"])
        self.char1.db_account = self.account
        self.char1.save(update_fields=["db_account"])

        result = object_admin._link_object_to_account(self.char1.id, self.account.id)

        self.assertTrue(result["linked"])
        self.assertIsInstance(result["message"], str)
        self.assertEqual(self.account.db._last_puppet, self.char1)
        self.assertIn(self.char1, self.account.characters)

    def test_link_service_rechecks_queued_actor_authorization(self):
        """A de-staffed actor cannot mutate after the worker-side gate passed."""
        self.account.is_staff = False
        self.account.save(update_fields=["is_staff"])
        self.account.db._last_puppet = None
        self.char1.db_account = self.account
        self.char1.save(update_fields=["db_account"])

        with self.assertRaises(PermissionError):
            object_admin._link_object_to_account(self.char1.id, self.account.id)

        self.assertIsNone(self.account.db._last_puppet)


class ObjectAdminTimeoutTest(SimpleTestCase):
    """Verify admin mutations never misreport an unknown result as failure."""

    def setUp(self):
        """Build the model admin and a scalar authenticated request."""
        self.model_admin = object_admin.ObjectAdmin(ObjectDB, admin.site)
        self.request = RequestFactory().post("/admin/objects/objectdb/account-object-link/1")
        self.request.user = SimpleNamespace(pk=9)

    def test_prestart_timeout_is_retryable(self):
        """A definitely cancelled mutation returns retryable 503."""
        with patch.object(
            object_admin, "run_on_io_thread", side_effect=IOThreadCallTimeout("cancelled")
        ):
            response = self.model_admin.link_object_to_account(self.request, 1)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "1")

    def test_started_timeout_is_nonretryable(self):
        """An unknown mutation outcome returns non-retryable 202."""
        with patch.object(
            object_admin,
            "run_on_io_thread",
            side_effect=IOThreadCallIndeterminate("unknown"),
        ):
            response = self.model_admin.link_object_to_account(self.request, 1)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["X-Evennia-Retryable"], "false")
