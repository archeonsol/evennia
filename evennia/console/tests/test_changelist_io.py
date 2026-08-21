"""ASGI worker regressions for concrete-field admin changelists."""

import asyncio
import threading

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django.test import AsyncClient, TransactionTestCase
from django.urls import reverse

from evennia.accounts.models import AccountDB
from evennia.comms.models import ChannelDB
from evennia.objects.models import ObjectDB
from evennia.utils import clock
from evennia.utils.create import create_account, create_channel, create_object


class AdminChangelistIOTest(TransactionTestCase):
    """Cold and warm changelists never consume canonical live objects."""

    def setUp(self):
        """Create one row of each type and authenticate a stock admin."""
        super().setUp()
        self.account = create_account(
            "ASGI admin",
            None,
            password="test-password",
            is_superuser=True,
        )
        self.account.is_staff = True
        self.account.save(update_fields=["is_staff"])
        self.listed_account = create_account(
            "ASGI listed account",
            None,
            password="test-password",
        )
        self.obj = create_object(key="ASGI object")
        self.channel = create_channel(key="ASGI channel")

        if self._testMethodName == "test_cold_changelists_stay_detached":
            ChannelDB.flush_instance_cache(force=True)
            ObjectDB.flush_instance_cache(force=True)
            AccountDB.flush_instance_cache(force=True)
        else:
            self.account = AccountDB.objects.get(pk=self.account.pk)
            self.listed_account = AccountDB.objects.get(pk=self.listed_account.pk)
            self.obj = ObjectDB.objects.get(pk=self.obj.pk)
            self.channel = ChannelDB.objects.get(pk=self.channel.pk)

    def tearDown(self):
        """Clear owner caches after each threaded request."""
        close_old_connections()
        ChannelDB.flush_instance_cache(force=True)
        ObjectDB.flush_instance_cache(force=True)
        AccountDB.flush_instance_cache(force=True)
        super().tearDown()

    async def _render_changelists(self, *, cold=False):
        """Render all affected changelists through Django's ASGI adapter."""
        client = AsyncClient()
        await client.aforce_login(self.account)
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        clock.bind_loop(loop)
        try:
            if cold:
                ChannelDB.flush_instance_cache(force=True)
                ObjectDB.flush_instance_cache(force=True)
                AccountDB.flush_instance_cache(force=True)
            responses = [
                await client.get(reverse(name))
                for name in (
                    "admin:accounts_accountdb_changelist",
                    "admin:objects_objectdb_changelist",
                    "admin:comms_channeldb_changelist",
                )
            ]
            cached_rows = (
                AccountDB.get_cached_instance(self.listed_account.pk),
                ObjectDB.get_cached_instance(self.obj.pk),
                ChannelDB.get_cached_instance(self.channel.pk),
            )
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()
        return responses, cached_rows

    async def test_cold_changelists_stay_detached(self):
        """Cold ASGI materialization renders without inserting owner cache rows."""
        responses, cached_rows = await self._render_changelists(cold=True)

        self.assertEqual([response.status_code for response in responses], [200, 200, 200])
        self.assertEqual(cached_rows, (None, None, None))

    async def test_warm_changelists_preserve_owner_identity(self):
        """ASGI workers cannot replace or receive already-cached owner rows."""
        owner_thread = threading.get_ident()
        cached = (self.listed_account, self.obj, self.channel)

        responses, cached_rows = await self._render_changelists()

        self.assertEqual([response.status_code for response in responses], [200, 200, 200])
        self.assertEqual(threading.get_ident(), owner_thread)
        self.assertIs(cached_rows[0], cached[0])
        self.assertIs(cached_rows[1], cached[1])
        self.assertIs(cached_rows[2], cached[2])
