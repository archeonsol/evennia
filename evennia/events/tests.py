"""
Tests for engine event bus.
"""

from django.test import override_settings

from evennia.events.bus import emit, subscribe
from evennia.server.models import GameEvent
from evennia.utils.test_resources import BaseEvenniaTest


class TestEventBus(BaseEvenniaTest):
    @override_settings(EVENT_BUS_ENABLED=True, EVENT_BUS_BACKEND="postgres")
    def test_emit_persists_to_postgres(self):
        seen = []

        def _listener(record):
            seen.append(record["subject"])

        subscribe("test.emit", _listener)
        emit("test.emit", {"value": 1}, persist=True)
        self.assertEqual(seen, ["test.emit"])
        self.assertEqual(GameEvent.objects.filter(subject="test.emit").count(), 1)

    def test_rejects_bad_subject(self):
        before = GameEvent.objects.count()
        emit("INVALID SUBJECT!", {})
        self.assertEqual(GameEvent.objects.count(), before)
