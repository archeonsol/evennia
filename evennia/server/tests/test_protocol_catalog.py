"""Loading the OOB event catalog, which gates what the server sends."""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from evennia.server import protocol


class TestLoadEventModules(SimpleTestCase):
    def setUp(self):
        loaded = patch.object(protocol, "_loaded", False)
        loaded.start()
        self.addCleanup(loaded.stop)

    @override_settings(PROTOCOL_EVENT_MODULES=["evennia.server.tests.no_such_event_module"])
    def test_a_broken_module_raises(self):
        with self.assertRaises(ImportError):
            protocol.load_event_modules()

    def test_a_failed_load_is_retried(self):
        with override_settings(
            PROTOCOL_EVENT_MODULES=["evennia.server.tests.no_such_event_module"]
        ):
            with self.assertRaises(ImportError):
                protocol.load_event_modules()
        with override_settings(PROTOCOL_EVENT_MODULES=["evennia.server.protocol.core_events"]):
            protocol.load_event_modules()
        self.assertTrue(protocol._loaded)
