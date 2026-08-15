"""Boundary tests for DTO-based stock website views."""

import asyncio
import threading
from dataclasses import fields, is_dataclass
from unittest.mock import patch

from django import forms
from django.db.models import Model, QuerySet
from django.test import AsyncClient, TransactionTestCase
from django.urls import reverse
from django.utils.text import slugify

from evennia.authorization.storage import grant_capability, resource_ref
from evennia.utils import class_from_module, clock
from evennia.utils.test_resources import BaseEvenniaTest
from evennia.web.utils.io import IOThreadCallIndeterminate, IOThreadCallTimeout
from evennia.web.website import forms as website_forms
from evennia.web.website.tests import EvenniaWebTest
from evennia.web.website.views import channels as channel_views
from evennia.web.website.views import objects as object_views
from evennia.web.website.views.characters import CharacterDetailView
from evennia.web.website.views.io import (
    ChannelWebDTO,
    ObjectWebDTO,
    _channel_dto,
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
            object_views, "run_on_io_thread", side_effect=IOThreadCallTimeout("cancelled")
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
            object_views,
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

    async def test_sync_view_returns_to_bound_io_loop(self):
        """The ASGI worker dispatches game-state reads to the bound loop."""
        loop = asyncio.get_running_loop()
        previous = clock.get_bound_loop()
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
            clock.bind_loop(previous)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed, [io_thread])
