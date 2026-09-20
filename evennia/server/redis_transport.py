"""Bounded forward-only Redis delivery with explicit publication outcomes."""

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings

from evennia.server import prometheus_metrics
from evennia.server.bus_result import PublicationResult, TransportUnavailable
from evennia.utils import clock, logger

MAX_ENTRIES = 4096
MAX_BYTES = 64 * 1024 * 1024
DATA_ENTRIES = 3072
DATA_BYTES = 48 * 1024 * 1024
OUTGOING_WARN_THRESHOLD = 200
OUTGOING_WARN_INTERVAL = 60.0
MAX_FRAME_BYTES = 8 * 1024 * 1024
STREAM_MAXLEN = 10000
MULTICAST_MAX_SESSIDS = 1024
STOP_TIMEOUT = 3.0
WAIT = 0.1
WRITE_BATCH_SIZE = 64
# Rejection reason for an ordinary queue-full publish. It is backpressure, not
# a transport fault, so it must not fence the bus the way a dead Redis does.
# Shared by the reject assignment and its check below.
CAPACITY_EXHAUSTED = "transport capacity exhausted"
READ_BATCH = 32


def _bus_limit(setting_name, fallback):
    """Resolve one bus limit: an explicit setting wins, else the module constant."""
    value = getattr(settings, setting_name, None)
    if value is None:
        return int(fallback)
    return max(1, int(value))


@dataclass(frozen=True)
class BusLimits:
    """All bus caps for one operation, resolved from settings or constants."""

    max_entries: int
    max_bytes: int
    data_entries: int
    data_bytes: int
    write_batch: int
    read_batch: int
    frame_bytes: int
    stream_maxlen: int
    multicast_chunk: int
    warn_threshold: int
    warn_interval: float

    def limit_pair(self, control):
        """Return the count and byte caps for one traffic class."""
        if control:
            return self.max_entries, self.max_bytes
        return self.data_entries, self.data_bytes


def resolve_limits():
    """Resolve every bus cap once into one object.

    Reading the module constants at call time keeps tests' patching of
    module-level limits authoritative while deployments tune the six
    settings-backed caps without a code change.
    """
    return BusLimits(
        max_entries=_bus_limit("REDIS_BUS_MAX_ENTRIES", MAX_ENTRIES),
        max_bytes=_bus_limit("REDIS_BUS_MAX_BYTES", MAX_BYTES),
        data_entries=_bus_limit("REDIS_BUS_DATA_ENTRIES", DATA_ENTRIES),
        data_bytes=_bus_limit("REDIS_BUS_DATA_BYTES", DATA_BYTES),
        write_batch=_bus_limit("REDIS_BUS_WRITE_BATCH", WRITE_BATCH_SIZE),
        read_batch=_bus_limit("REDIS_BUS_READ_BATCH", READ_BATCH),
        frame_bytes=int(MAX_FRAME_BYTES),
        stream_maxlen=int(STREAM_MAXLEN),
        multicast_chunk=int(MULTICAST_MAX_SESSIDS),
        warn_threshold=int(OUTGOING_WARN_THRESHOLD),
        warn_interval=float(OUTGOING_WARN_INTERVAL),
    )


def encode_pair(pair):
    """Produce a stable wire representation of the negotiated pair."""
    return json.dumps(pair, separators=(",", ":")).encode() if pair else b""


def _stream_id(value):
    """Compare explicit Redis stream IDs numerically."""
    if isinstance(value, bytes):
        value = value.decode()
    return tuple(int(part) for part in value.split("-"))


@dataclass(slots=True)
class _Outgoing:
    """One admitted frame, retained through publication callback settlement."""

    stream: str
    command: bytes
    data: bytes
    pair: bytes
    fence: int
    handshake: bool
    result: PublicationResult

    @property
    def size(self):
        """Count encoded payload and bounded wire metadata."""
        return len(self.command) + len(self.data) + len(self.pair) + 128


class RedisTransport:
    """Own bounded queues, workers, and an immediately invalidatable fence."""

    def __init__(self, read_stream, on_frame, *, on_failure=None, on_recovered=None):
        """Bind event-loop handlers without opening a connection."""
        self._url = getattr(settings, "REDIS_BUS_URL", "redis://127.0.0.1:6379/1")
        self._read_stream = read_stream
        self._on_frame = on_frame
        self._on_failure = on_failure or (lambda reason: None)
        self._on_recovered = on_recovered or (lambda: None)
        self._client = None
        self._writer = self._reader = self._cleanup = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._outgoing = {}
        self._outgoing_bytes = 0
        self._next_outgoing_warning = 0.0
        self._ordinary = deque()
        self._handshakes = deque()
        self._incoming = deque()
        self._incoming_bytes = 0
        self._incoming_active = 0
        self._drain_scheduled = False
        self._next_id = 0
        self._writing = set()
        self._fence = 0
        self._origin = uuid4().hex.encode()
        self._sequence = 0
        self._pair = b""
        self._ready = True
        self.online = False
        self._failure_delivered = True
        self._failure_reason = None
        self._recover_scheduled = False
        self._cursor = b"0-0"
        self._read_origin = None
        self._read_sequence = None

    @property
    def outgoing_count(self):
        """Include in-flight writes and pending completion callbacks."""
        with self._lock:
            return len(self._outgoing)

    @property
    def outgoing_bytes(self):
        """Return encoded outgoing bytes still owned by this transport."""
        with self._lock:
            return self._outgoing_bytes

    def set_pair(self, pair, ready=False):
        """Bind ordinary traffic while allowing discovery to pass startup output."""
        with self._condition:
            self._pair = encode_pair(pair)
            self._ready = ready
            stale = [
                key
                for key, frame in self._outgoing.items()
                if not frame.handshake and frame.pair != self._pair
            ]
            self._ordinary = deque(key for key in self._ordinary if key not in stale)
            self._condition.notify_all()
        if stale:
            clock.call_from_thread(self._reject_stale, stale, _task_kind="transport")

    def _reject_stale(self, keys):
        """Settle replaced-generation frames without releasing in-flight bytes."""
        for key in keys:
            with self._lock:
                frame = self._outgoing.get(key)
                if key not in self._writing:
                    removed = self._outgoing.pop(key, None)
                    if removed is not None:
                        self._outgoing_bytes -= removed.size
            if frame is not None:
                frame.result.fail(TransportUnavailable("connection generation replaced"))

    def _baseline(self):
        """Capture the current tail once before a fresh discovery exchange."""
        entries = self._client.xrevrange(self._read_stream, count=1)
        if entries:
            self._cursor, fields = entries[0]
            self._read_origin = fields.get(b"o")
            self._read_sequence = int(fields[b"n"]) if b"n" in fields else None
        else:
            self._cursor = b"0-0"
            self._read_origin = self._read_sequence = None

    def start(self):
        """Start one worker pair and reject overlap with prior cleanup."""
        workers = (self._writer, self._reader, self._cleanup)
        alive = [worker is not None and worker.is_alive() for worker in workers]
        if any(alive):
            if all(alive[:2]) and not alive[2] and not self._stop.is_set():
                return False
            raise RuntimeError("redis bus: previous workers or cleanup still running")
        if not self._failure_delivered:
            raise RuntimeError("redis bus: previous failure settlement pending")
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        self._client = redis.Redis.from_url(
            self._url,
            socket_connect_timeout=1,
            socket_timeout=1,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
        )
        self._cleanup = None
        try:
            self._client.ping()
            self._baseline()
        except redis.exceptions.RedisError as error:
            logger.log_err(f"redis bus: cannot reach Redis on boot ({error}); stopping process")
            clock.stop_loop()
            return False
        with self._condition:
            self._stop.clear()
            self.online = True
            self._failure_delivered = True
        self._writer = threading.Thread(
            target=self._writer_loop, name="redis-bus-writer", daemon=True
        )
        self._reader = threading.Thread(
            target=self._reader_loop, name="redis-bus-reader", daemon=True
        )
        self._writer.start()
        self._reader.start()
        return True

    def publish(self, stream, command, data, *, pair=None, handshake=False):
        """Admit one frame without implying publication or peer execution."""
        data = data if data is not None else b""
        control = handshake or command.startswith((b"Admin", b"Bus"))
        frame = _Outgoing(
            stream, command, data, encode_pair(pair), self._fence, handshake, PublicationResult()
        )
        reason = None
        warning = None
        with self._condition:
            limits = resolve_limits()
            if self._stop.is_set() or not self.online:
                reason = "transport unavailable"
            elif len(data) > limits.frame_bytes:
                reason = "encoded frame exceeds transport limit"
            else:
                count = len(self._outgoing)
                size = self._outgoing_bytes
                count_limit, byte_limit = limits.limit_pair(control)
                if count >= count_limit or size + frame.size > byte_limit:
                    reason = CAPACITY_EXHAUSTED
                else:
                    self._next_id += 1
                    self._outgoing[self._next_id] = frame
                    self._outgoing_bytes += frame.size
                    (self._handshakes if handshake else self._ordinary).append(self._next_id)
                    self._condition.notify_all()
                    count += 1
                    size += frame.size
                if count > limits.warn_threshold:
                    now = time.monotonic()
                    if now >= self._next_outgoing_warning:
                        self._next_outgoing_warning = now + limits.warn_interval
                        warning = (
                            f"redis bus: outgoing queue pressure stream={stream} "
                            f"pending={count} bytes={size} "
                            f"data_limit={limits.data_entries}"
                        )
        if warning:
            logger.log_warn(warning)
        if reason:
            try:
                prometheus_metrics.record_bus_reject(reason)
            except Exception as err:
                logger.log_warn(f"redis bus: reject telemetry failed: {err}")
            if control and self.online and reason != CAPACITY_EXHAUSTED:
                self.fail(reason)
            return PublicationResult.rejected(TransportUnavailable(reason))
        return frame.result

    def fail(self, reason):
        """Fence stale work immediately, before notifying the event loop."""
        with self._condition:
            if not self.online:
                return
            self.online = False
            self._fence += 1
            self._origin = uuid4().hex.encode()
            self._sequence = 0
            self._ordinary.clear()
            self._handshakes.clear()
            self._incoming.clear()
            self._incoming_bytes = self._incoming_active
            self._failure_delivered = False
            self._failure_reason = reason
            fence = self._fence
            self._condition.notify_all()
        clock.call_from_thread(self._deliver_failure, reason, fence, _task_kind="transport")

    def settle_failure(self):
        """Settle a final abort before the event loop exits."""
        self._deliver_failure(self._failure_reason, self._fence)

    def _deliver_failure(self, reason, fence):
        """Settle all failed work on the loop before allowing fresh discovery."""
        error = TransportUnavailable(f"{reason}; interrupted work may have run")
        with self._lock:
            if fence != self._fence or self._failure_delivered:
                return
            frames = list(self._outgoing.values())
            self._outgoing = {
                key: frame for key, frame in self._outgoing.items() if key in self._writing
            }
            self._outgoing_bytes = sum(frame.size for frame in self._outgoing.values())
        for frame in frames:
            frame.result.fail(error)
        try:
            self._on_failure(reason)
        finally:
            with self._condition:
                self._failure_delivered = True
                self._condition.notify_all()

    def _complete(self, key, fence, entry_id, error):
        """Release one accounted publication on the event loop."""
        with self._lock:
            frame = self._outgoing.pop(key, None)
            if frame is not None:
                self._outgoing_bytes -= frame.size
            valid = fence == self._fence and self.online and not self._stop.is_set()
            valid = valid and frame is not None and (frame.handshake or frame.pair == self._pair)
        if frame is None:
            return
        if error is not None or not valid:
            frame.result.fail(
                TransportUnavailable("publication uncertain; interrupted work may have run")
            )
        else:
            frame.result.succeed(entry_id)

    def _writer_loop(self):
        """Publish admitted frames in wire order through batched pipelines."""
        while not self._stop.is_set():
            batch = self._take_write_batch()
            if batch:
                self._write_batch(batch)

    def _take_write_batch(self):
        """Reserve up to one batch of frames for a single Redis round trip."""
        batch = []
        batch_size = resolve_limits().write_batch
        with self._condition:
            if not self.online or not (self._handshakes or (self._ready and self._ordinary)):
                self._condition.wait(WAIT)
                return batch
            # Handshake frames go in their own batch: a failed publication must
            # not take discovery down with a failed data publication.
            handshake_only = bool(self._handshakes)
            while len(batch) < batch_size:
                if handshake_only:
                    if not self._handshakes:
                        break
                    queue = self._handshakes
                elif self._ready and self._ordinary:
                    queue = self._ordinary
                else:
                    break
                key = queue.popleft()
                frame = self._outgoing.get(key)
                if frame is None or frame.fence != self._fence:
                    continue
                self._writing.add(key)
                self._sequence += 1
                batch.append(
                    (
                        key,
                        frame,
                        {
                            b"c": frame.command,
                            b"d": frame.data,
                            b"g": frame.pair,
                            b"o": self._origin,
                            b"n": str(self._sequence).encode(),
                        },
                    )
                )
            return batch

    def _write_batch(self, batch):
        """Publish one batch through one pipeline and settle it on the loop."""
        responses = None
        limits = resolve_limits()
        try:
            pipeline = self._client.pipeline(transaction=False)
            for _key, frame, fields in batch:
                pipeline.xadd(frame.stream, fields, maxlen=limits.stream_maxlen, approximate=True)
            responses = pipeline.execute(raise_on_error=False)
        except Exception as caught:
            responses = [caught] * len(batch)
            self.fail("Redis publication failed")
        with self._condition:
            self._writing.difference_update(key for key, _frame, _fields in batch)
            self._condition.notify_all()
        completions = []
        published = 0
        failed = 0
        first_error = None
        for (key, frame, _fields), response in zip(batch, responses):
            if isinstance(response, BaseException):
                failed += 1
                if first_error is None:
                    first_error = response
                completions.append((key, frame.fence, None, response))
            else:
                completions.append((key, frame.fence, response, None))
                published += 1
        # Settlement is scheduled first: it outranks escalation and telemetry,
        # neither of which may strand a published batch unsettled.
        clock.call_from_thread(self._complete_many, completions, _task_kind="transport")
        if failed:
            # raise_on_error=False parks per-command failures in the responses;
            # those frames did not publish, so escalate as the pre-batching
            # per-command path did.
            logger.log_warn(
                f"redis bus: {failed}/{len(batch)} XADD commands failed: {first_error!r}"
            )
            self.fail("Redis publication failed")
        try:
            with self._condition:
                depth, size = len(self._outgoing), self._outgoing_bytes
            prometheus_metrics.observe_bus_queue(depth, size)
            prometheus_metrics.record_bus_write_batch(len(batch))
            prometheus_metrics.record_bus_publish(published)
        except Exception as err:
            logger.log_warn(f"redis bus: write telemetry failed: {err}")

    def _complete_many(self, completions):
        """Settle one published batch on the event loop in publication order."""
        for key, fence, entry_id, error in completions:
            self._complete(key, fence, entry_id, error)

    def _admit_incoming(self, fields):
        """Bound callback payloads and reject stale negotiated pairs at admission."""
        limits = resolve_limits()
        command, data = fields.get(b"c"), fields.get(b"d")
        if command is None or data is None or len(data) > limits.frame_bytes:
            self.fail("invalid or oversized stream payload")
            return
        handshake = command == b"BusHandshake"
        size = sum(len(key) + len(value) for key, value in fields.items())
        schedule = False
        with self._lock:
            if not self.online or (not handshake and fields.get(b"g", b"") != self._pair):
                return
            control = handshake or command.startswith((b"Admin", b"Bus"))
            count_limit, byte_limit = limits.limit_pair(control)
            full = (
                len(self._incoming) + bool(self._incoming_active) >= count_limit
                or self._incoming_bytes + size > byte_limit
            )
            if not full:
                self._incoming.append(
                    (
                        self._fence,
                        command,
                        data,
                        fields.get(b"g", b""),
                        size,
                        time.monotonic(),
                    )
                )
                self._incoming_bytes += size
                if not self._drain_scheduled:
                    self._drain_scheduled = schedule = True
        if full:
            self.fail("incoming callback capacity exhausted")
        elif schedule:
            clock.call_from_thread(self._drain_incoming, _task_kind="transport")

    def _drain_incoming(self):
        """Execute at most 32 FIFO frames per event-loop turn.

        Records how long each frame waited between the stream read and this
        reactor turn: that wait is the portal→server command latency (and the
        server→portal output latency seen from the portal), the gap a saturated
        reactor opens without showing up in any per-command timer.
        """
        for _ in range(32):
            with self._lock:
                if not self._incoming:
                    self._drain_scheduled = False
                    return
                fence, command, data, pair, size, admitted_at = self._incoming.popleft()
                self._incoming_active = size
                valid = self.online and fence == self._fence and not self._stop.is_set()
                valid = valid and (command == b"BusHandshake" or pair == self._pair)
                depth = len(self._incoming) + bool(self._incoming_active)
                pending_bytes = self._incoming_bytes
            try:
                prometheus_metrics.observe_bus_incoming_queue(depth, pending_bytes)
                prometheus_metrics.record_bus_incoming_wait(time.monotonic() - admitted_at)
            except Exception as err:
                logger.log_warn(f"redis bus: incoming telemetry failed: {err}")
            try:
                if valid:
                    self._on_frame(command, data)
            except Exception:
                self.fail("incoming frame application failed")
                logger.log_trace("redis bus: incoming frame application failed")
            finally:
                with self._lock:
                    self._incoming_bytes -= size
                    self._incoming_active = 0
        clock.call_from_thread(self._drain_incoming, _task_kind="transport")

    def _history_intact(self):
        """Detect deletion or trimming before making subsequent frames executable."""
        if self._cursor == b"0-0":
            return True
        info = self._client.xinfo_stream(self._read_stream)
        first = info.get("first-entry")
        return first is not None and _stream_id(first[0]) <= _stream_id(self._cursor)

    def _recover(self):
        """Reopen only after old work settles and Redis establishes a fresh tail."""
        with self._lock:
            allowed = self._failure_delivered and not self._writing and not self._recover_scheduled
        if not allowed:
            self._stop.wait(WAIT)
            return
        try:
            self._client.ping()
            self._baseline()
        except Exception:
            self._stop.wait(WAIT)
            return
        with self._lock:
            self._recover_scheduled = True
            fence = self._fence
        clock.call_from_thread(self._recovered, fence, _task_kind="transport")
        self._stop.wait(WAIT)

    def _recovered(self, fence):
        """Publish connectivity to the loop before starting fresh discovery."""
        with self._condition:
            self._recover_scheduled = False
            if self._stop.is_set() or fence != self._fence:
                return
            self.online = True
            self._condition.notify_all()
        self._on_recovered()

    def _reader_loop(self):
        """Read only forward from an explicit cursor, with no pending reclaim."""
        while not self._stop.is_set():
            if not self.online:
                self._recover()
                continue
            try:
                if not self._history_intact():
                    self.fail("transport stream history was trimmed")
                    continue
                response = self._client.xread(
                    {self._read_stream: self._cursor},
                    count=resolve_limits().read_batch,
                    block=250,
                )
                for _stream, entries in response or []:
                    for entry_id, fields in entries:
                        if self._stop.is_set() or not self.online:
                            break
                        origin, sequence = fields.get(b"o"), int(fields.get(b"n", b"0"))
                        if not origin or not sequence:
                            self.fail("stream frame missing delivery identity")
                            break
                        if (origin != self._read_origin and sequence != 1) or (
                            origin == self._read_origin
                            and self._read_sequence is not None
                            and sequence != self._read_sequence + 1
                        ):
                            self.fail("transport stream sequence gap")
                            break
                        self._read_origin, self._read_sequence = origin, sequence
                        self._cursor = entry_id
                        self._admit_incoming(fields)
            except Exception:
                if not self._stop.is_set():
                    self.fail("Redis read failed")

    def _close_after_workers(self, workers, client):
        """Close the captured client after its workers relinquish ownership."""
        for worker in workers:
            if worker is not None:
                worker.join()
        try:
            if client is not None:
                client.close()
        except Exception:
            logger.log_trace("redis bus: client cleanup failed")

    def stop(self):
        """Abort within one deadline without requiring queue or loop progress."""
        deadline = time.monotonic() + STOP_TIMEOUT
        self.fail("transport stopped")
        with self._condition:
            self._stop.set()
            self._condition.notify_all()
        if self._cleanup is None and any(
            item is not None for item in (self._writer, self._reader, self._client)
        ):
            self._cleanup = threading.Thread(
                target=self._close_after_workers,
                args=((self._writer, self._reader), self._client),
                name="redis-bus-cleanup",
                daemon=True,
            )
            self._cleanup.start()
        if self._cleanup is not None:
            self._cleanup.join(timeout=max(0, deadline - time.monotonic()))
        for name in ("_writer", "_reader", "_cleanup"):
            worker = getattr(self, name)
            if worker is not None and worker.is_alive():
                logger.log_warn(f"redis bus: {name[1:]} still running; restart blocked")
            elif name != "_cleanup":
                setattr(self, name, None)
