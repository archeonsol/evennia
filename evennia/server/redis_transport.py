"""Bounded forward-only Redis delivery with explicit publication outcomes."""

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings

from evennia.server.bus_result import PublicationResult, TransportUnavailable
from evennia.utils import clock, logger

MAX_ENTRIES = 256
MAX_BYTES = 32 * 1024 * 1024
DATA_ENTRIES = 224
DATA_BYTES = 24 * 1024 * 1024
OUTGOING_WARN_THRESHOLD = 200
OUTGOING_WARN_INTERVAL = 60.0
MAX_FRAME_BYTES = 8 * 1024 * 1024
STOP_TIMEOUT = 3.0
WAIT = 0.1


def encode_pair(pair):
    """Produce a stable wire representation of the negotiated pair."""
    return json.dumps(pair, separators=(",", ":")).encode() if pair else b""


def _stream_id(value):
    """Compare explicit Redis stream IDs numerically."""
    if isinstance(value, bytes):
        value = value.decode()
    return tuple(int(part) for part in value.split("-"))


@dataclass
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
        self._next_outgoing_warning = 0.0
        self._ordinary = deque()
        self._handshakes = deque()
        self._incoming = deque()
        self._incoming_bytes = 0
        self._incoming_active = 0
        self._drain_scheduled = False
        self._next_id = 0
        self._writing = None
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
            return sum(frame.size for frame in self._outgoing.values())

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
            clock.call_from_thread(self._reject_stale, stale)

    def _reject_stale(self, keys):
        """Settle replaced-generation frames without releasing in-flight bytes."""
        for key in keys:
            with self._lock:
                frame = self._outgoing.get(key)
                if key != self._writing:
                    self._outgoing.pop(key, None)
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
            if self._stop.is_set() or not self.online:
                reason = "transport unavailable"
            elif len(data) > MAX_FRAME_BYTES:
                reason = "encoded frame exceeds transport limit"
            else:
                count = len(self._outgoing)
                size = sum(item.size for item in self._outgoing.values())
                count_limit = MAX_ENTRIES if control else DATA_ENTRIES
                byte_limit = MAX_BYTES if control else DATA_BYTES
                if count >= count_limit or size + frame.size > byte_limit:
                    reason = "transport capacity exhausted"
                else:
                    self._next_id += 1
                    self._outgoing[self._next_id] = frame
                    (self._handshakes if handshake else self._ordinary).append(self._next_id)
                    self._condition.notify_all()
                    count += 1
                    size += frame.size
                if count > OUTGOING_WARN_THRESHOLD:
                    now = time.monotonic()
                    if now >= self._next_outgoing_warning:
                        self._next_outgoing_warning = now + OUTGOING_WARN_INTERVAL
                        warning = (
                            f"redis bus: outgoing queue pressure stream={stream} "
                            f"pending={count} bytes={size} data_limit={DATA_ENTRIES}"
                        )
        if warning:
            logger.log_warn(warning)
        if reason:
            if control and self.online:
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
        clock.call_from_thread(self._deliver_failure, reason, fence)

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
                key: frame for key, frame in self._outgoing.items() if key == self._writing
            }
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
        """Publish each selected frame once in actual wire sequence order."""
        while not self._stop.is_set():
            with self._condition:
                if not self.online or not (self._handshakes or (self._ready and self._ordinary)):
                    self._condition.wait(WAIT)
                    continue
                key = (self._handshakes if self._handshakes else self._ordinary).popleft()
                frame = self._outgoing.get(key)
                if frame is None or frame.fence != self._fence:
                    continue
                self._writing = key
                self._sequence += 1
                fields = {
                    b"c": frame.command,
                    b"d": frame.data,
                    b"g": frame.pair,
                    b"o": self._origin,
                    b"n": str(self._sequence).encode(),
                }
            entry_id = error = None
            try:
                entry_id = self._client.xadd(frame.stream, fields, maxlen=10000, approximate=True)
            except Exception as caught:
                error = caught
                self.fail("Redis publication failed")
            finally:
                with self._condition:
                    self._writing = None
                    self._condition.notify_all()
                clock.call_from_thread(self._complete, key, frame.fence, entry_id, error)

    def _admit_incoming(self, fields):
        """Bound callback payloads and reject stale negotiated pairs at admission."""
        command, data = fields.get(b"c"), fields.get(b"d")
        if command is None or data is None or len(data) > MAX_FRAME_BYTES:
            self.fail("invalid or oversized stream payload")
            return
        handshake = command == b"BusHandshake"
        size = sum(len(key) + len(value) for key, value in fields.items())
        schedule = False
        with self._lock:
            if not self.online or (not handshake and fields.get(b"g", b"") != self._pair):
                return
            control = handshake or command.startswith((b"Admin", b"Bus"))
            count_limit = MAX_ENTRIES if control else DATA_ENTRIES
            byte_limit = MAX_BYTES if control else DATA_BYTES
            full = (
                len(self._incoming) + bool(self._incoming_active) >= count_limit
                or self._incoming_bytes + size > byte_limit
            )
            if not full:
                self._incoming.append((self._fence, command, data, fields.get(b"g", b""), size))
                self._incoming_bytes += size
                if not self._drain_scheduled:
                    self._drain_scheduled = schedule = True
        if full:
            self.fail("incoming callback capacity exhausted")
        elif schedule:
            clock.call_from_thread(self._drain_incoming)

    def _drain_incoming(self):
        """Execute at most 32 FIFO frames per event-loop turn."""
        for _ in range(32):
            with self._lock:
                if not self._incoming:
                    self._drain_scheduled = False
                    return
                fence, command, data, pair, size = self._incoming.popleft()
                self._incoming_active = size
                valid = self.online and fence == self._fence and not self._stop.is_set()
                valid = valid and (command == b"BusHandshake" or pair == self._pair)
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
        clock.call_from_thread(self._drain_incoming)

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
            allowed = (
                self._failure_delivered and self._writing is None and not self._recover_scheduled
            )
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
        clock.call_from_thread(self._recovered, fence)
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
                response = self._client.xread({self._read_stream: self._cursor}, count=1, block=250)
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
