"""The live feed.

A console for a running server should show the server running. This is the
piece that makes the difference between a database viewer and an operations
surface: metrics that move, log lines as they are written, health that changes
under you.

Transport: server-sent events, not a websocket
----------------------------------------------

The plan specified a websocket carrying the Azaban envelope. This ships SSE
instead, and the reasoning is worth recording because the discipline is
unchanged and only the pipe differs.

* **The feed is one-directional.** Everything the console sends -- actions,
  saves, queries -- goes over ordinary POSTs that already work, are already
  authorized, and are already audited. A websocket would buy bidirectionality
  the feed never uses.
* **Django's ASGI application is HTTP-only.** ``get_asgi_application`` rejects
  a websocket scope, so one would need an ASGI wrapper plus hand-rolled
  session-cookie authentication -- reimplementing, less carefully, what
  ``ConsolePermission`` already does.
* **Reconnection comes free and correct.** ``EventSource`` reconnects on its
  own and replays through ``Last-Event-ID``, which is exactly the
  replay-across-reconnect behaviour the plan wanted, without writing it.
* **The Portal boundary stays clean.** Azaban is a Portal concern serving
  players. The console is a Server-side staff surface. Sharing the encoder
  would have coupled them.

What survives from the Azaban discipline: a typed ``t`` discriminant on every
frame, a monotonic sequence number, a bounded replay buffer, batching, and
structural byte caps enforced before anything is sent.

Cost
----

A stream exists only while somebody is watching it, so an unattended console
costs nothing at all -- there is no polling loop and no unconditional write.
While one is open, each producer samples on its own interval, and every
producer here reads plain state: no producer touches the IO owner, so the feed
keeps working when the game server is down, exactly like the panels do.

"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from django.conf import settings

from evennia.console import health
from evennia.utils import logger

#: Frames held for replay after a reconnect. One buffer per stream.
REPLAY_BUFFER = 256

#: Largest payload one frame may carry, before encoding.
MAX_FRAME_BYTES = 64 * 1024

#: Frames coalesced into one batch before flushing.
MAX_BATCH = 24

#: Seconds a stream waits between producer sweeps.
TICK_SECONDS = 1.0

#: Seconds between keepalive comments when nothing is happening. Below any
#: sensible proxy idle timeout.
KEEPALIVE_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class Frame:
    """One typed event on the feed.

    Attributes:
        t: Topic discriminant, e.g. ``"health"`` or ``"log"``.
        s: Monotonic sequence number, used for replay after a reconnect.
        data: JSON-safe payload.
    """

    t: str
    s: int
    data: dict

    def encode(self) -> str:
        """Render this frame as one SSE event."""

        body = json.dumps({"t": self.t, "s": self.s, **self.data}, default=str)
        if len(body) > MAX_FRAME_BYTES:
            body = json.dumps({"t": self.t, "s": self.s, "truncated": True, "bytes": len(body)})
        return f"id: {self.s}\nevent: {self.t}\ndata: {body}\n\n"


class Producer:
    """One source of frames.

    Subclasses implement :meth:`sample` and are asked for frames no more often
    than :attr:`interval`. A producer must be cheap, must not raise, and must
    not touch the IO owner -- the feed has to survive the game server being
    down, or it stops being useful exactly when it matters.
    """

    key = ""
    interval = 5.0

    def bind(self, actor_id):
        """Tell this producer whose stream it is feeding.

        Almost every producer reports on the server and ignores this. A watch
        feed cannot: one operator's captured traffic must not appear on
        another's stream, so it needs to know who is listening.

        Args:
            actor_id: Account primary key of the console user.
        """

    def sample(self):
        """Return payload dicts to emit, or an empty sequence.

        Returns:
            Sequence[dict]: Zero or more payloads.
        """

        return ()

    def safe_sample(self):
        """Sample without letting one broken producer end the stream."""

        try:
            return list(self.sample() or ())
        except Exception:  # noqa: BLE001 - one producer must not kill the feed
            logger.log_trace(f"console feed producer {self.key!r} failed")
            return []


class HealthProducer(Producer):
    """Emit the health summary, and say when it changes."""

    key = "health"
    interval = 5.0

    def __init__(self):
        """Start with no previous reading."""

        self._previous = None

    def sample(self):
        """Return the current health, marking a change from the last one."""

        state = health.status()
        changed = self._previous is not None and state["checks"] != self._previous
        self._previous = state["checks"]
        return [{**state, "changed": changed}]


class MetricsProducer(Producer):
    """Emit current values from the Prometheus registry.

    Optional by construction: the metrics module already degrades when
    ``prometheus_client`` is absent, so this reports that once and goes quiet
    rather than failing every tick.
    """

    key = "metrics"
    interval = 5.0
    prefix = "evennia_"

    def __init__(self):
        """Assume the registry is available until proven otherwise."""

        self._unavailable_reported = False

    def sample(self):
        """Return one frame carrying every engine metric sample."""

        try:
            from prometheus_client import REGISTRY
        except ImportError:
            if self._unavailable_reported:
                return []
            self._unavailable_reported = True
            return [
                {
                    "available": False,
                    "reason": "prometheus_client is not installed, so no metrics are collected.",
                    "samples": [],
                }
            ]

        samples = []
        for metric in REGISTRY.collect():
            if not metric.name.startswith(self.prefix):
                continue
            for sample in metric.samples:
                samples.append(
                    {
                        "name": sample.name,
                        "labels": dict(sample.labels or {}),
                        "value": sample.value,
                    }
                )
        return [{"available": True, "samples": samples}]


class LogProducer(Producer):
    """Emit log lines as they are written.

    Reads by tracking a byte offset per file rather than reusing
    ``tail_log_file``, which answers "the last N lines" through a Twisted
    deferred. A continuous tail wants the opposite shape: plain synchronous
    reads of whatever arrived since last time, with the offset reset when a
    file is rotated out from under it.
    """

    key = "log"
    interval = 1.0

    #: Lines one sweep may emit per file, so a burst cannot flood the stream.
    MAX_LINES = 40

    #: Bytes of the file's start used as a replacement signature.
    HEAD_BYTES = 256

    def __init__(self, files=None):
        """Track the tail position and identity of each configured log file."""

        #: path -> ``{"id", "head", "offset"}``.
        #:
        #: Three signals, because log files are replaced in three different
        #: ways and no single one catches all of them. A rename-and-recreate
        #: changes the inode. A truncate in place keeps the inode and can
        #: leave the file *longer* than the old offset, so a size comparison
        #: misses it. Only the head signature catches that, and without it the
        #: tail reads from the middle of a fresh file and emits half a line as
        #: though it were new.
        self._positions = {}
        self._files = files if files is not None else self._configured_files()

    def _configured_files(self):
        """Return the log files this deployment writes."""

        names = (
            "SERVER_LOG_FILE",
            "PORTAL_LOG_FILE",
            "HTTP_LOG_FILE",
            "LOCKWARNING_LOG_FILE",
        )
        found = {}
        for name in names:
            path = getattr(settings, name, "")
            if path:
                found[name.replace("_LOG_FILE", "").lower()] = path
        return found

    def sample(self):
        """Return the lines appended to each file since the last sweep."""

        import os

        frames = []
        for label, path in self._files.items():
            try:
                stat = os.stat(path)
            except OSError:
                continue
            identity = (stat.st_ino, stat.st_dev)
            size = stat.st_size

            previous = self._positions.get(path)
            if previous is None:
                # First sight of the file: start at the end. A console opening
                # mid-incident wants what happens next, not the whole history
                # replayed at it.
                self._positions[path] = self._position(path, identity, size)
                continue

            offset = previous["offset"]
            # Recomputed over exactly the prefix recorded last time. A growing
            # prefix would change on every append and read as a replacement.
            head = self._head(path, previous["head_len"])
            if previous["id"] != identity or previous["head"] != head or size < offset:
                offset = 0
            if size == offset:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    text = handle.read(MAX_FRAME_BYTES)
                    self._positions[path] = self._position(path, identity, handle.tell())
            except OSError:
                continue
            lines = [line for line in text.splitlines() if line.strip()]
            if not lines:
                continue
            for line in lines[-self.MAX_LINES :]:
                frames.append({"source": label, "line": line[:2000]})
        return frames

    def _position(self, path, identity, offset):
        """Record where this file has been read to, and what it looked like."""

        head_len = min(self.HEAD_BYTES, offset)
        return {
            "id": identity,
            "offset": offset,
            "head_len": head_len,
            "head": self._head(path, head_len),
        }

    def _head(self, path, length):
        """Return a signature of one file's first ``length`` bytes.

        The length is fixed at the point it was recorded rather than read
        fresh each time. Appending to a short file would otherwise lengthen the
        prefix, change the signature, and be mistaken for a replacement.
        """

        import hashlib

        if length <= 0:
            return ""
        try:
            with open(path, "rb") as handle:
                return hashlib.blake2b(handle.read(length), digest_size=8).hexdigest()
        except OSError:
            return ""


#: Producers the engine ships, by topic key.
class WatchProducer(Producer):
    """Emit the traffic captured by this operator's session watches.

    A push source on a pull feed. The taps run on the IO owner and append to a
    bounded deque; this drains that deque on each sweep, so the two never meet
    and the producer contract -- cheap, never raises, never touches the IO
    owner -- holds unchanged.

    Scoped to one operator. Two staff watching two different sessions do not
    see each other's capture, and neither sees anything at all before they
    start a watch of their own.
    """

    key = "watch"
    interval = 1.0

    def __init__(self):
        self._actor_id = 0

    def bind(self, actor_id):
        """Record whose watches this stream drains."""

        self._actor_id = int(actor_id or 0)

    def sample(self):
        """Return the frames captured since the last sweep."""

        if not self._actor_id:
            return ()
        from evennia.console import watch

        return watch.drain(self._actor_id)


BUILTIN_PRODUCERS = (HealthProducer, MetricsProducer, LogProducer, WatchProducer)


@dataclass
class Stream:
    """One connected console's view of the feed.

    Attributes:
        topics: Topic keys this stream carries.
        sequence: Next sequence number to hand out.
        buffer: Recent frames, for replay after a reconnect.
    """

    topics: tuple[str, ...]
    #: Account primary key of the console user this stream belongs to. Only the
    #: watch feed needs it, and it needs it absolutely: without it one
    #: operator's capture would land on every open console.
    actor_id: int = 0
    sequence: int = 0
    buffer: list = field(default_factory=list)
    _producers: list = field(default_factory=list)
    _next_due: dict = field(default_factory=dict)

    def __post_init__(self):
        """Build the producers this stream's topics need."""

        wanted = set(self.topics)
        for producer_class in BUILTIN_PRODUCERS:
            if producer_class.key in wanted:
                producer = producer_class()
                producer.bind(self.actor_id)
                self._producers.append(producer)
                self._next_due[producer.key] = 0.0

    def replay(self, last_seq):
        """Return buffered frames after one sequence number.

        Args:
            last_seq: The last sequence the client received, or ``None``.

        Returns:
            list[Frame]: Frames the client missed, oldest first.
        """

        if last_seq is None:
            return []
        return [frame for frame in self.buffer if frame.s > last_seq]

    def due(self, now=None):
        """Return frames from every producer whose interval has elapsed."""

        now = now if now is not None else time.monotonic()
        frames = []
        for producer in self._producers:
            if now < self._next_due.get(producer.key, 0.0):
                continue
            self._next_due[producer.key] = now + producer.interval
            for payload in producer.safe_sample()[:MAX_BATCH]:
                frames.append(self._frame(producer.key, payload))
        return frames

    def _frame(self, topic, payload):
        """Stamp one payload and record it for replay."""

        self.sequence += 1
        frame = Frame(topic, self.sequence, payload)
        self.buffer.append(frame)
        if len(self.buffer) > REPLAY_BUFFER:
            del self.buffer[: len(self.buffer) - REPLAY_BUFFER]
        return frame


def available_topics() -> tuple[str, ...]:
    """Return every topic key a client may subscribe to."""

    return tuple(sorted(producer.key for producer in BUILTIN_PRODUCERS))


def parse_topics(requested) -> tuple[str, ...]:
    """Return the valid topics in a request, or every topic by default.

    Args:
        requested: Comma-separated topic keys, or empty.

    Returns:
        tuple[str, ...]: Topics to carry, in a stable order.
    """

    known = set(available_topics())
    names = [name.strip() for name in str(requested or "").split(",") if name.strip()]
    chosen = tuple(sorted(name for name in names if name in known))
    return chosen or available_topics()
