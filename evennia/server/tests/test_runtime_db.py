"""Tests for asyncio database-scope enforcement."""

import asyncio
import os
from unittest.mock import patch

from django.db import connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from evennia.server import runtime_db
from evennia.utils import clock


class RuntimeDatabaseAuditTest(SimpleTestCase):
    """Unmanaged loop connections are visible and optionally rejected."""

    def test_synchronous_connection_context_is_outside_runtime_policy(self):
        with patch.object(runtime_db.logger, "log_warn") as warning:
            runtime_db.audit_runtime_connection(None, None)
        warning.assert_not_called()

    def test_managed_runtime_context_is_allowed(self):
        async def scenario():
            token = clock._runtime_task_kind.set("test")
            try:
                with patch.object(runtime_db.logger, "log_warn") as warning:
                    runtime_db.audit_runtime_connection(None, None)
                warning.assert_not_called()
            finally:
                clock._runtime_task_kind.reset(token)

        asyncio.run(scenario())


class RuntimeDatabaseIntegrationTest(TransactionTestCase):
    """A real task-owned backend connection is closed at root completion."""

    def test_runtime_root_closes_real_connection(self):
        observed = {}
        close_patch = None

        async def root():
            nonlocal close_patch
            wrapper = connections["default"]
            wrapper.ensure_connection()
            close_patch = patch.object(wrapper, "close", wraps=wrapper.close)
            observed["close"] = close_patch.start()
            observed["wrapper"] = wrapper
            self.assertIsNotNone(wrapper.connection)

        async def scenario():
            await clock.run_coroutine(root(), task_kind="test")

        try:
            with patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"}):
                asyncio.run(scenario())
        finally:
            if close_patch is not None:
                close_patch.stop()

        observed["close"].assert_called_once_with()
        if observed["wrapper"].vendor != "sqlite":
            self.assertIsNone(observed["wrapper"].connection)

    @override_settings(ENGINE_RUNTIME_UNMANAGED_DB_POLICY="error")
    def test_strict_policy_rejects_unmanaged_loop_connection(self):
        async def scenario():
            with self.assertRaises(RuntimeError):
                runtime_db.audit_runtime_connection(None, None)

        asyncio.run(scenario())
