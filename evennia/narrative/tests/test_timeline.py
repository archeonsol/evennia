"""Tests for bounded universal narrative sinks and timeline replay."""

import unittest

from evennia.narrative.rendernode import text_node
from evennia.narrative.timeline import (
    MAX_TIMELINE_ENTRIES,
    recent_deliveries,
    record_delivery,
    register_sink,
    unregister_sink,
)


class _NDB:
    pass


class _Viewer:
    def __init__(self):
        self.ndb = _NDB()


class _Sink:
    def __init__(self):
        self.nodes = []

    def accept(self, node, viewer):
        self.nodes.append((node, viewer))


class TestNarrativeTimeline(unittest.TestCase):
    def test_records_bounded_semantic_timeline(self):
        viewer = _Viewer()
        for index in range(MAX_TIMELINE_ENTRIES + 10):
            record_delivery(text_node(str(index)), viewer)
        entries = recent_deliveries(viewer, limit=MAX_TIMELINE_ENTRIES)
        self.assertEqual(len(entries), MAX_TIMELINE_ENTRIES)
        self.assertEqual(entries[-1].body, str(MAX_TIMELINE_ENTRIES + 9))

    def test_registered_sink_receives_immutable_node(self):
        viewer = _Viewer()
        sink = _Sink()
        register_sink("camera-test", sink)
        try:
            node = text_node("A figure enters.", kind="movement")
            record_delivery(node, viewer)
            self.assertIs(sink.nodes[0][0], node)
            self.assertIs(sink.nodes[0][1], viewer)
        finally:
            unregister_sink("camera-test")


if __name__ == "__main__":
    unittest.main()
