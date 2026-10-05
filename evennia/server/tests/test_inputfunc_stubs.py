"""Inputfuncs that only exist so a client's routine message is not logged as an error."""

from django.test import SimpleTestCase

from evennia.server import inputfuncs
from evennia.utils.utils import callables_from_module


class ClientStubInputfuncTests(SimpleTestCase):
    STUBS = (
        "client_gui",
        "client_name",
        "client_version",
        "external_discord_hello",
        "external_discord_get",
        "supports_add",
        "supports_remove",
        "ping",
    )

    def test_each_stub_is_registered_as_an_inputfunc(self):
        registered = callables_from_module("evennia.server.inputfuncs")

        for name in self.STUBS:
            with self.subTest(name=name):
                self.assertIn(name, registered)

    def test_calling_one_does_nothing_and_takes_any_arguments(self):
        for name in self.STUBS:
            with self.subTest(name=name):
                self.assertIsNone(getattr(inputfuncs, name)(object(), "a", key="b"))
