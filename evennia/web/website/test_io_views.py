"""Boundary tests for DTO-based stock website views."""

import asyncio
import threading
from dataclasses import fields, is_dataclass
from unittest.mock import patch

from asgiref.sync import sync_to_async
from django import forms
from django.conf import settings
from django.db import close_old_connections
from django.db.models import Model, QuerySet
from django.test import AsyncClient, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils.text import slugify

from evennia.authorization.storage import grant_capability, resource_ref
from evennia.utils import class_from_module, clock
from evennia.utils.create import create_account, create_object
from evennia.utils.test_resources import BaseEvenniaTest
from evennia.web.utils import general_context
from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    run_on_io_thread,
)
from evennia.web.website import forms as website_forms
from evennia.web.website.tests import EvenniaWebTest
from evennia.web.website.views import channels as channel_views
from evennia.web.website.views import characters as character_views
from evennia.web.website.views import objects as object_views
from evennia.web.website.views.characters import (
    CharacterDetailView,
    CharacterListView,
    CharacterManageView,
)
from evennia.web.website.views.io import (
    ChannelWebDTO,
    CharacterListWebDTO,
    ObjectWebDTO,
    _channel_dto,
    load_character_page,
    load_object_detail,
    typeclass_path,
)


def _assert_plain(testcase, value):
    """Assert recursively that a service result contains no live Django state."""
    testcase.assertNotIsInstance(value, (Model, QuerySet))
    if is_dataclass(value):
        testcase.assertTrue(value.__dataclass_params__.frozen)
        for field in fields(value):
            _assert_plain(testcase, getattr(value, field.name))
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_plain(testcase, key)
            _assert_plain(testcase, item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_plain(testcase, item)


class StockWebsiteDTOTest(EvenniaWebTest):
    """Verify stock routes render frozen DTOs rather than typeclasses."""

    def setUp(self):
        """Attach a character and authenticate the web client."""
        super().setUp()
        self.account.is_superuser = True
        self.account.save(update_fields=["is_superuser"])
        grant_capability(
            f"account:{self.account.pk}",
            "engine.object.view",
            scope_kind="resource",
            scope_key=resource_ref(self.char1),
            provenance="test",
        )
        self.assertTrue(self.login())

    def test_character_detail_context_is_plain(self):
        """Character detail exposes a frozen object DTO."""
        direct = load_object_detail(
            typeclass_path(CharacterDetailView.model),
            self.char1.pk,
            slugify(self.char1.name),
            self.account.pk,
            "view",
            ("name", "desc"),
        )
        self.assertIsInstance(direct, ObjectWebDTO)
        response = self.client.get(
            reverse(
                "character-detail",
                kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
            )
        )
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertIsInstance(response.context["object"], ObjectWebDTO)
        _assert_plain(self, response.context["object"])

    def test_character_update_form_is_not_model_backed(self):
        """Worker-side validation cannot query or materialize ObjectDB."""
        self.assertTrue(issubclass(website_forms.CharacterUpdateForm, forms.Form))
        self.assertFalse(issubclass(website_forms.CharacterUpdateForm, forms.ModelForm))

    def test_character_create_form_is_not_model_backed(self):
        """Create validation cannot materialize a character on the worker."""
        self.assertTrue(issubclass(website_forms.CharacterForm, forms.Form))
        self.assertFalse(issubclass(website_forms.CharacterForm, forms.ModelForm))

    def test_character_list_and_manage_contexts_are_plain(self):
        """Both character collections paginate frozen DTO rows."""
        for route, view_type in (
            ("characters", CharacterListView),
            ("character-manage", CharacterManageView),
        ):
            with (
                self.subTest(route=route),
                patch.object(character_views, "run_on_io_thread", wraps=run_on_io_thread) as bridge,
                patch.object(general_context, "run_on_io_thread") as menu_bridge,
            ):
                response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200, response.content.decode())
            rows = list(response.context["object_list"])
            self.assertTrue(rows)
            self.assertTrue(all(isinstance(row, CharacterListWebDTO) for row in rows))
            _assert_plain(self, rows)
            bridge.assert_called_once()
            menu_bridge.assert_not_called()

    def test_character_collection_timeouts_are_retryable_gateway_timeouts(self):
        """Both list routes classify either read timeout identically."""
        for route in ("characters", "character-manage"):
            for error in (IOThreadCallTimeout(), IOThreadCallIndeterminate()):
                with (
                    self.subTest(route=route, error=type(error).__name__),
                    patch.object(character_views, "run_on_io_thread", side_effect=error),
                ):
                    response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 504)
                self.assertEqual(response.headers["X-Evennia-Retryable"], "true")

    @override_settings(MAX_NR_CHARACTERS=5)
    def test_create_puppet_and_delete_each_dispatch_once(self):
        """Reachable character mutations never fall through inherited model views."""
        with patch.object(character_views, "run_on_io_thread", wraps=run_on_io_thread) as bridge:
            created = self.client.post(
                reverse("character-create"),
                {"db_key": "Bridge Created", "desc": "plain"},
            )
        self.assertEqual(created.status_code, 302)
        bridge.assert_called_once()

        with patch.object(character_views, "run_on_io_thread", wraps=run_on_io_thread) as bridge:
            puppeted = self.client.post(
                reverse(
                    "character-puppet",
                    kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
                )
            )
        self.assertEqual(puppeted.status_code, 302)
        self.assertEqual(self.client.session["puppet"], self.char1.pk)
        bridge.assert_called_once()

        self.assertEqual(
            self.client.get(
                reverse(
                    "character-puppet",
                    kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
                )
            ).status_code,
            405,
        )

        with patch.object(character_views, "run_on_io_thread", wraps=run_on_io_thread) as bridge:
            deleted = self.client.post(
                reverse(
                    "character-delete",
                    kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
                )
            )
        self.assertEqual(deleted.status_code, 302)
        bridge.assert_called_once()

    @override_settings(MAX_NR_CHARACTERS=5)
    def test_create_timeout_never_follows_success_redirect(self):
        """Create distinguishes a cancelled mutation from an unknown outcome."""
        cases = (
            (IOThreadCallTimeout(), 503, "true"),
            (IOThreadCallIndeterminate(), 202, "false"),
        )
        for error, expected_status, retryable in cases:
            with (
                self.subTest(error=type(error).__name__),
                patch.object(character_views, "run_on_io_thread", side_effect=error),
            ):
                response = self.client.post(
                    reverse("character-create"),
                    {"db_key": "Timed Out", "desc": "plain"},
                )
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(response.headers["X-Evennia-Retryable"], retryable)
            self.assertNotIn("Location", response.headers)

    def test_puppet_timeout_does_not_change_web_session(self):
        """Late authorization reads cannot select a session puppet."""
        session = self.client.session
        session["puppet"] = None
        session.save()
        with patch.object(
            character_views,
            "run_on_io_thread",
            side_effect=IOThreadCallIndeterminate(),
        ):
            response = self.client.post(
                reverse(
                    "character-puppet",
                    kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
                )
            )
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.headers["X-Evennia-Retryable"], "true")
        self.assertIsNone(self.client.session.get("puppet"))

    def test_character_read_timeout_is_gateway_timeout(self):
        """A stock detail read never reports a timeout as a missing object."""
        with patch.object(
            object_views, "run_on_io_thread", side_effect=IOThreadCallTimeout("cancelled")
        ):
            response = self.client.get(
                reverse(
                    "character-detail",
                    kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
                )
            )
        self.assertEqual(response.status_code, 504)

    def test_character_prestart_mutation_timeout_is_retryable(self):
        """A cancelled update reports that it is safe to retry."""
        with patch.object(
            character_views, "run_on_io_thread", side_effect=IOThreadCallTimeout("cancelled")
        ):
            response = self.client.post(
                reverse(
                    "character-update",
                    kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
                ),
                {"db_key": self.char1.key, "desc": "new description"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "1")

    def test_character_started_mutation_timeout_is_nonretryable(self):
        """An update with unknown outcome never redirects or invites retry."""
        with patch.object(
            character_views,
            "run_on_io_thread",
            side_effect=IOThreadCallIndeterminate("unknown"),
        ):
            response = self.client.post(
                reverse(
                    "character-update",
                    kwargs={"pk": self.char1.pk, "slug": slugify(self.char1.name)},
                ),
                {"db_key": self.char1.key, "desc": "new description"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["X-Evennia-Retryable"], "false")
        self.assertNotIn("Location", response.headers)

    def test_channel_list_context_is_plain(self):
        """Channel rows contain only frozen serialized values."""
        from django.conf import settings

        channel_typeclass = class_from_module(
            settings.BASE_CHANNEL_TYPECLASS, fallback=settings.FALLBACK_CHANNEL_TYPECLASS
        )
        channel, errors = channel_typeclass.create("DTO channel")
        self.assertFalse(errors)
        channel.db_lock_storage = "listen:all()"
        channel.save(update_fields=["db_lock_storage"])
        grant_capability(
            f"account:{self.account.pk}",
            "engine.channel.listen",
            scope_kind="resource",
            scope_key=resource_ref(channel),
            provenance="test",
        )
        dto = _channel_dto(channel)
        _assert_plain(self, dto)
        with patch.object(channel_views, "load_channel_list", return_value=(dto,)):
            response = self.client.get(reverse("channels"))
        self.assertEqual(response.status_code, 200, response.content.decode())
        rows = list(response.context["object_list"])
        self.assertTrue(rows)
        self.assertTrue(all(isinstance(row, ChannelWebDTO) for row in rows))
        _assert_plain(self, rows)

    def test_channel_detail_preserves_name_attribute(self):
        """The DTO keeps the stock channel detail's advertised Name row."""
        from django.conf import settings

        channel_typeclass = class_from_module(
            settings.BASE_CHANNEL_TYPECLASS, fallback=settings.FALLBACK_CHANNEL_TYPECLASS
        )
        channel, errors = channel_typeclass.create("Detail DTO channel")
        self.assertFalse(errors)
        dto = _channel_dto(channel, include_log=True)
        with patch.object(channel_views, "load_channel_detail", return_value=dto):
            response = self.client.get(
                reverse("channel-detail", kwargs={"slug": slugify(channel.key)})
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["attribute_list"], {"Name": "Detail DTO channel"})


class StockWebsiteASGIBoundaryTest(TransactionTestCase):
    """Exercise a synchronous stock view through Django's ASGI worker."""

    reset_sequences = True

    def setUp(self):
        """Create one public channel before binding the runtime loop."""
        super().setUp()
        from django.conf import settings

        channel_typeclass = class_from_module(
            settings.BASE_CHANNEL_TYPECLASS, fallback=settings.FALLBACK_CHANNEL_TYPECLASS
        )
        channel, errors = channel_typeclass.create("ASGI channel")
        self.assertFalse(errors)
        channel.db_lock_storage = "listen:all()"
        channel.save(update_fields=["db_lock_storage"])

        self.account = create_account(
            "ASGI web owner",
            None,
            password="test-password",
        )
        self.character = create_object(settings.BASE_CHARACTER_TYPECLASS, key="ASGI Character")
        self.account.characters.add(self.character)
        grant_capability(
            f"account:{self.account.pk}",
            "engine.object.view",
            scope_kind="resource",
            scope_key=resource_ref(self.character),
            provenance="test",
        )

    async def test_sync_view_returns_to_bound_io_loop(self):
        """The ASGI worker dispatches game-state reads to the bound loop."""
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        io_thread = threading.get_ident()
        observed = []
        original = channel_views.load_channel_list

        def record_loop(*args, **kwargs):
            observed.append(threading.get_ident())
            return original(*args, **kwargs)

        clock.bind_loop(loop)
        try:
            with patch.object(channel_views, "load_channel_list", side_effect=record_loop):
                response = await AsyncClient().get("/channels/")
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed, [io_thread])

    @override_settings(MAX_NR_CHARACTERS=5)
    async def test_character_reads_and_create_return_to_exact_bound_loop(self):
        """Authenticated sync routes hand read and mutation work to the exact loop."""
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
        previous_thread = clock.get_loop_thread_id()
        io_thread = threading.get_ident()
        callbacks = []
        workers = []
        original_page = load_character_page
        original_create = character_views.create_character

        def record_page(*args, **kwargs):
            callbacks.append(("page", threading.get_ident(), asyncio.get_running_loop()))
            return original_page(*args, **kwargs)

        def record_create(*args, **kwargs):
            callbacks.append(("create", threading.get_ident(), asyncio.get_running_loop()))
            return original_create(*args, **kwargs)

        def record_worker(*args, **kwargs):
            workers.append(threading.get_ident())
            return run_on_io_thread(*args, **kwargs)

        client = AsyncClient()
        await client.aforce_login(self.account)
        clock.bind_loop(loop)
        try:
            with (
                patch.object(character_views, "load_character_page", side_effect=record_page),
                patch.object(character_views, "create_character", side_effect=record_create),
                patch.object(character_views, "run_on_io_thread", side_effect=record_worker),
            ):
                listed = await client.get("/characters/")
                managed = await client.get("/characters/manage/")
                created = await client.post(
                    "/characters/create/",
                    {"db_key": "ASGI Created", "desc": "plain"},
                )
                with patch.object(
                    character_views,
                    "run_on_io_thread",
                    side_effect=IOThreadCallIndeterminate(),
                ):
                    timed_out = await client.post(
                        "/characters/create/",
                        {"db_key": "ASGI Unknown", "desc": "plain"},
                    )
        finally:
            clock._main_loop = previous
            clock._loop_thread_id = previous_thread
            await sync_to_async(close_old_connections, thread_sensitive=True)()

        self.assertEqual(
            [listed.status_code, managed.status_code, created.status_code, timed_out.status_code],
            [200, 200, 302, 202],
        )
        self.assertEqual(timed_out.headers["X-Evennia-Retryable"], "false")
        for response in (listed, managed):
            _assert_plain(self, list(response.context["object_list"]))
        self.assertEqual([kind for kind, _thread, _loop in callbacks], ["page", "page", "create"])
        self.assertTrue(
            all(thread == io_thread and seen_loop is loop for _, thread, seen_loop in callbacks)
        )
        self.assertEqual(len(workers), 3)
        self.assertTrue(all(worker != io_thread for worker in workers))
