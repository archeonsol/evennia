"""Tests for the shared process event-loop factory."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from evennia.utils import loop_factory


class NewProcessLoopTest(SimpleTestCase):
    def test_prefers_uvloop_when_installed(self):
        fake_uvloop = MagicMock()
        sentinel = object()
        fake_uvloop.new_event_loop.return_value = sentinel
        with patch.dict("sys.modules", {"uvloop": fake_uvloop}):
            loop = loop_factory.new_process_loop()
        self.assertIs(loop, sentinel)
        fake_uvloop.new_event_loop.assert_called_once_with()

    def test_falls_back_to_asyncio(self):
        import asyncio

        # A None entry in sys.modules makes ``import uvloop`` raise ImportError,
        # modelling a platform or install without uvloop (e.g. Windows).
        with patch.dict("sys.modules", {"uvloop": None}):
            loop = loop_factory.new_process_loop()
        try:
            self.assertIsInstance(loop, asyncio.AbstractEventLoop)
        finally:
            loop.close()


class LoopNameTest(SimpleTestCase):
    def test_labels_uvloop_and_asyncio(self):
        import asyncio

        fake_loop_cls = type("Loop", (), {"__module__": "uvloop.loop"})
        self.assertEqual(loop_factory.loop_name(fake_loop_cls()), "uvloop")

        loop = asyncio.new_event_loop()
        try:
            self.assertEqual(loop_factory.loop_name(loop), "asyncio")
        finally:
            loop.close()
