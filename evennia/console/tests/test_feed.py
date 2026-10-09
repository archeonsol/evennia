"""Tests for the live feed.

The feed holds a connection open, which makes its failure modes different from
a request's. A producer that raises must not end the stream. A reconnect must
not lose frames or replay ones already seen. A burst must not become an
unbounded write. And none of it may touch the IO owner, or the feed dies at the
moment it is most wanted.

"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from evennia.console import feed, watch
from evennia.console.feed import (
    REPLAY_BUFFER,
    Frame,
    HealthProducer,
    LogProducer,
    MetricsProducer,
    Producer,
    Stream,
    WatchProducer,
    available_topics,
    parse_topics,
)


class TestFrame(SimpleTestCase):
    """Frames are typed, sequenced, and bounded."""

    def test_encodes_as_one_sse_event(self):
        text = Frame("health", 3, {"healthy": True}).encode()
        self.assertIn("id: 3\n", text)
        self.assertIn("event: health\n", text)
        self.assertTrue(text.endswith("\n\n"))

    def test_payload_carries_the_discriminant_and_sequence(self):
        text = Frame("log", 7, {"line": "x"}).encode()
        body = json.loads(text.split("data: ", 1)[1].strip())
        self.assertEqual(body["t"], "log")
        self.assertEqual(body["s"], 7)
        self.assertEqual(body["line"], "x")

    def test_an_oversized_frame_is_replaced_not_sent(self):
        # A frame larger than the cap would otherwise be written in full to
        # every connected console.
        frame = Frame("log", 1, {"line": "x" * (feed.MAX_FRAME_BYTES + 100)})
        body = json.loads(frame.encode().split("data: ", 1)[1].strip())
        self.assertTrue(body["truncated"])
        self.assertNotIn("line", body)

    def test_unserializable_values_do_not_raise(self):
        text = Frame("t", 1, {"when": object()}).encode()
        self.assertIn("data: ", text)


class TestTopics(SimpleTestCase):
    """Subscription is validated, never trusted."""

    def test_defaults_to_everything(self):
        self.assertEqual(parse_topics(""), available_topics())
        self.assertEqual(parse_topics(None), available_topics())

    def test_selects_known_topics(self):
        self.assertEqual(parse_topics("log,health"), ("health", "log"))

    def test_unknown_topics_are_dropped(self):
        self.assertEqual(parse_topics("log,nonsense"), ("log",))

    def test_all_unknown_falls_back_to_everything(self):
        self.assertEqual(parse_topics("nonsense"), available_topics())


class _Counter(Producer):
    """A producer that emits one incrementing payload per sweep."""

    key = "health"
    interval = 0.0

    def __init__(self):
        self.calls = 0

    def sample(self):
        self.calls += 1
        return [{"n": self.calls}]


class _Broken(Producer):
    """A producer that always raises."""

    key = "metrics"
    interval = 0.0

    def sample(self):
        raise RuntimeError("the producer is broken")


class TestStream(SimpleTestCase):
    """Sequencing, replay, and isolation between producers."""

    def _stream(self, *producers):
        """Build a stream driven by the given producers."""
        stream = Stream(topics=tuple(p.key for p in producers))
        stream._producers = list(producers)
        stream._next_due = {p.key: 0.0 for p in producers}
        return stream

    def test_sequence_numbers_are_monotonic(self):
        stream = self._stream(_Counter())
        first = stream.due(now=1.0)
        second = stream.due(now=2.0)
        self.assertEqual([f.s for f in first], [1])
        self.assertEqual([f.s for f in second], [2])

    def test_a_producer_is_not_sampled_before_its_interval(self):
        producer = _Counter()
        producer.interval = 10.0
        stream = self._stream(producer)
        stream.due(now=0.0)
        stream.due(now=1.0)
        self.assertEqual(producer.calls, 1)
        stream.due(now=20.0)
        self.assertEqual(producer.calls, 2)

    def test_a_broken_producer_does_not_end_the_stream(self):
        # The whole point: one bad sampler must not take the connection with
        # it, because the connection is how the operator sees anything.
        counter = _Counter()
        stream = self._stream(_Broken(), counter)
        frames = stream.due(now=1.0)
        self.assertEqual([f.t for f in frames], ["health"])
        self.assertEqual(counter.calls, 1)

    def test_replay_returns_only_what_was_missed(self):
        stream = self._stream(_Counter())
        stream.due(now=1.0)
        stream.due(now=2.0)
        stream.due(now=3.0)
        self.assertEqual([f.s for f in stream.replay(1)], [2, 3])
        self.assertEqual(stream.replay(3), [])

    def test_a_fresh_client_gets_no_replay(self):
        stream = self._stream(_Counter())
        stream.due(now=1.0)
        self.assertEqual(stream.replay(None), [])

    def test_the_replay_buffer_is_bounded(self):
        stream = self._stream(_Counter())
        for tick in range(REPLAY_BUFFER + 50):
            stream.due(now=float(tick))
        self.assertEqual(len(stream.buffer), REPLAY_BUFFER)
        # The newest frames are the ones kept.
        self.assertEqual(stream.buffer[-1].s, REPLAY_BUFFER + 50)

    def test_only_subscribed_topics_produce(self):
        stream = Stream(topics=("health",))
        self.assertEqual({p.key for p in stream._producers}, {"health"})


class TestWatchProducer(SimpleTestCase):
    """A live stream drains only the operator it was built for."""

    def test_the_stream_binds_the_operator_to_the_watch_producer(self):
        with patch.object(watch, "drain", return_value=[]) as drain:
            stream = Stream(topics=("watch",), actor_id=42)
            stream.due(now=1.0)
        drain.assert_called_once_with(42)

    def test_an_anonymous_stream_never_drains_watch_traffic(self):
        with patch.object(watch, "drain", return_value=[]) as drain:
            producer = WatchProducer()
            self.assertEqual(producer.sample(), ())
        drain.assert_not_called()


class TestHealthProducer(TestCase):
    """Health reports, and marks when it changed."""

    def test_first_sample_is_not_a_change(self):
        payload = HealthProducer().sample()[0]
        self.assertFalse(payload["changed"])
        self.assertIn("checks", payload)

    def test_a_change_is_marked(self):
        producer = HealthProducer()
        with patch("evennia.console.health.io_available", return_value=True):
            producer.sample()
        with patch("evennia.console.health.io_available", return_value=False):
            payload = producer.sample()[0]
        self.assertTrue(payload["changed"])
        self.assertTrue(payload["degraded"])

    def test_a_steady_state_is_not_a_change(self):
        producer = HealthProducer()
        producer.sample()
        self.assertFalse(producer.sample()[0]["changed"])


class TestMetricsProducer(SimpleTestCase):
    """Metrics are optional, and their absence is stated once."""

    def test_absence_is_reported_once_then_goes_quiet(self):
        producer = MetricsProducer()
        with patch.dict("sys.modules", {"prometheus_client": None}):
            first = producer.sample()
            second = producer.sample()
        self.assertFalse(first[0]["available"])
        self.assertIn("not installed", first[0]["reason"])
        self.assertEqual(second, [])


class _Collector:
    """A Prometheus collector that counts how often it was asked for its metric."""

    def __init__(self, name, value=1.0):
        self.name = name
        self.value = value
        self.collected = 0

    def _family(self):
        from prometheus_client.core import GaugeMetricFamily

        return GaugeMetricFamily(self.name, "a test metric")

    def describe(self):
        return [self._family()]

    def collect(self):
        self.collected += 1
        family = self._family()
        family.add_metric([], self.value)
        yield family


class TestRestrictedCollectionLosesNothing(SimpleTestCase):
    """Asking only the engine's collectors must return what the full collection would have."""

    def test_the_same_series_come_back_as_from_collecting_everything(self):
        try:
            from prometheus_client import REGISTRY
        except ImportError:
            self.skipTest("prometheus_client is not installed")
        from evennia.server import prometheus_metrics

        if not prometheus_metrics._init_metrics():
            self.skipTest("metrics are disabled")
        prometheus_metrics.record_gc_pause(2, 0.01)  # a labelled series, so labels are compared
        prometheus_metrics.record_reactor_stall(0.3, "gil")

        def series(samples):
            return {(s["name"], tuple(sorted(s["labels"].items()))) for s in samples}

        everything = [
            {"name": sample.name, "labels": dict(sample.labels or {})}
            for metric in REGISTRY.collect()
            if metric.name.startswith("evennia_")
            for sample in metric.samples
        ]
        restricted = feed._collect_samples(REGISTRY, "evennia_")

        self.assertGreater(len(everything), 50)
        self.assertEqual(series(restricted), series(everything))


class TestMetricsSampling(SimpleTestCase):
    """The feed reads the engine's own metrics, once for everyone watching."""

    def setUp(self):
        super().setUp()
        try:
            import prometheus_client
            from prometheus_client import CollectorRegistry
        except ImportError:
            self.skipTest("prometheus_client is not installed")
        self.prometheus_client = prometheus_client
        self.registry = CollectorRegistry()
        feed._shared_metrics.clear()
        self.addCleanup(feed._shared_metrics.clear)
        self.clock = [100.0]
        for patcher in (
            patch.object(prometheus_client, "REGISTRY", self.registry),
            patch.object(feed, "_monotonic", lambda: self.clock[0]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _register(self, name, value=1.0):
        collector = _Collector(name, value)
        self.registry.register(collector)
        return collector

    def test_samples_carry_the_name_labels_and_value(self):
        self._register("evennia_queue_depth", 3.0)

        payload = MetricsProducer().sample()[0]

        self.assertTrue(payload["available"])
        self.assertEqual(
            payload["samples"],
            [{"name": "evennia_queue_depth", "labels": {}, "value": 3.0}],
        )

    def test_only_the_engines_own_collectors_are_asked(self):
        """Each stream used to run every collector in the process, five times a minute."""
        engine = self._register("evennia_queue_depth", 3.0)
        other = self._register("django_http_requests_total", 9.0)

        payload = MetricsProducer().sample()[0]

        self.assertEqual({s["name"] for s in payload["samples"]}, {"evennia_queue_depth"})
        self.assertEqual((engine.collected, other.collected), (1, 0))

    def test_a_registry_that_cannot_list_its_names_is_collected_whole_and_filtered(self):
        from prometheus_client.core import GaugeMetricFamily

        engine, other = GaugeMetricFamily("evennia_a", "a"), GaugeMetricFamily("other_b", "b")
        engine.add_metric([], 1.0)
        other.add_metric([], 2.0)
        bare = SimpleNamespace(collect=lambda: iter([engine, other]))

        with patch.object(self.prometheus_client, "REGISTRY", bare):
            payload = MetricsProducer().sample()[0]

        self.assertEqual([s["name"] for s in payload["samples"]], ["evennia_a"])

    def test_streams_share_one_sample_inside_half_an_interval(self):
        engine = self._register("evennia_queue_depth")
        first, second = MetricsProducer(), MetricsProducer()

        first.sample()
        self.clock[0] += 1.0
        second.sample()
        self.assertEqual(engine.collected, 1)

        self.clock[0] += 2.0  # three seconds after the first
        second.sample()

        self.assertEqual(engine.collected, 2)

    def test_a_sample_is_not_shared_with_a_different_registry(self):
        """A registry swapped under the feed (a test, a reload) must not be served stale data."""
        from prometheus_client import CollectorRegistry

        self._register("evennia_queue_depth", 1.0)
        MetricsProducer().sample()
        other = CollectorRegistry()
        other.register(_Collector("evennia_queue_depth", 7.0))

        with patch.object(self.prometheus_client, "REGISTRY", other):
            payload = MetricsProducer().sample()[0]

        self.assertEqual(payload["samples"][0]["value"], 7.0)

    def test_concurrent_streams_do_not_collect_twice(self):
        import threading

        engine = self._register("evennia_queue_depth")
        results = []

        def watch():
            results.append(MetricsProducer().sample()[0]["samples"])

        threads = [threading.Thread(target=watch) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(engine.collected, 1)
        self.assertEqual(len(results), 6)


class TestLogProducer(SimpleTestCase):
    """Tailing, including the cases that break naive tailers."""

    def setUp(self):
        import tempfile

        self.directory = tempfile.mkdtemp()
        self.path = f"{self.directory}/test.log"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("first\nsecond\n")
        self.producer = LogProducer(files={"test": self.path})

    def _append(self, text):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(text)

    def test_first_sweep_starts_at_the_end(self):
        # A console opened mid-incident wants what happens next, not the
        # whole history replayed at it.
        self.assertEqual(self.producer.sample(), [])

    def test_new_lines_are_emitted_once(self):
        self.producer.sample()
        self._append("third\n")
        frames = self.producer.sample()
        self.assertEqual([f["line"] for f in frames], ["third"])
        self.assertEqual(self.producer.sample(), [])

    def test_truncation_in_place_is_detected(self):
        # The hard case a size check misses: the file is rewritten under the
        # same name and ends up LONGER than the old offset. Without a head
        # signature the tail reads from the middle and emits half a line.
        self.producer.sample()
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("after truncation, and rather longer than before\n")
        frames = self.producer.sample()
        self.assertEqual(
            [f["line"] for f in frames],
            ["after truncation, and rather longer than before"],
        )

    def test_truncation_to_a_shorter_file_is_detected(self):
        self.producer.sample()
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("s\n")
        self.assertEqual([f["line"] for f in self.producer.sample()], ["s"])

    def test_rename_rotation_is_detected(self):
        # The ordinary case: the old file moves aside, a new one is created.
        import os

        self.producer.sample()
        os.replace(self.path, f"{self.path}.1")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("fresh file\n")
        frames = self.producer.sample()
        self.assertEqual([f["line"] for f in frames], ["fresh file"])

    def test_a_burst_is_capped(self):
        self.producer.sample()
        self._append("".join(f"line {n}\n" for n in range(500)))
        frames = self.producer.sample()
        self.assertLessEqual(len(frames), LogProducer.MAX_LINES)

    def test_a_missing_file_is_skipped(self):
        producer = LogProducer(files={"gone": f"{self.directory}/nope.log"})
        self.assertEqual(producer.sample(), [])

    def test_long_lines_are_truncated(self):
        self.producer.sample()
        self._append("x" * 5000 + "\n")
        frames = self.producer.sample()
        self.assertLessEqual(len(frames[0]["line"]), 2000)

    @override_settings(SERVER_LOG_FILE="", PORTAL_LOG_FILE="", HTTP_LOG_FILE="")
    def test_configured_files_come_from_settings(self):
        producer = LogProducer()
        self.assertNotIn("server", producer._files)


class TestNoIOOwner(TestCase):
    """No producer touches the IO owner."""

    def test_producers_do_not_cross_the_bridge(self):
        # The feed has to survive the game server being down; a producer that
        # crossed the bridge would kill the stream exactly when it matters.
        from evennia.web.utils import io

        with patch.object(io, "run_on_io_thread", side_effect=AssertionError("bridge used")):
            stream = Stream(topics=available_topics())
            stream.due(now=1.0)
