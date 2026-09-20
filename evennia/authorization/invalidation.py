"""Push-based authorization invalidation over a durable Redis Stream.

Authorization facts (principal grants/suspension, resource labels, policy
packages) are cached per process and keyed by generation. Mutations publish a
small event to one durable Redis Stream instead of relying on every process
polling Redis generation counters on a TTL:

* the mutating process applies its own change locally first, so in-process
  read-your-writes is immediate;
* it appends ``(revision, namespace, ref, generation)`` to the stream, where
  ``revision`` is a global ``INCR`` counter used only for gap detection;
* every authorization process reads the full stream from its own persisted
  cursor (plain ``XREAD``, never a consumer group, so each process sees every
  event) and applies generations monotonically, dropping affected snapshots;
* a gap (an event revision more than one past the applied revision) or a
  revision that advanced without a corresponding event (failed append, dead
  reader) triggers a reconcile: the local fact caches are flushed and refilled
  lazily, so a missed event can never serve a stale snapshot indefinitely;
* a slow periodic anti-entropy check compares the applied revision against the
  global revision as disaster recovery.

Plain pub/sub is deliberately not used: reloads and connection losses discard
events. The stream is trimmed approximately; trimming is safe because gap
detection reconciles whenever events were lost.

The stream is disabled by default. Enable it per game with
``AUTHORIZATION_PUSH_INVALIDATION = True``; the polling fallback in
``storage`` keeps working unchanged when it is off.
"""

from __future__ import annotations

import threading
import time

from django.conf import settings

from evennia.utils import logger
from evennia.utils.utils import cached_setting

__all__ = [
    "applied_revision",
    "publish_generation",
    "push_enabled",
    "reconcile",
    "start",
    "stop",
]


def _setting(name, default):
    return cached_setting(name, default)


def push_enabled() -> bool:
    """Return whether this process publishes and consumes invalidation events."""

    return bool(_setting("AUTHORIZATION_PUSH_INVALIDATION", False))


def _stream_key() -> str:
    return str(_setting("AUTHORIZATION_INVALIDATION_STREAM", "evennia:authz:invalidation"))


def _revision_key() -> str:
    return str(_setting("AUTHORIZATION_INVALIDATION_REVISION_KEY", "evennia:authz:revision"))


def _cursor_key() -> str:
    return str(_setting("AUTHORIZATION_INVALIDATION_CURSOR_KEY", "evennia:authz:cursor"))


def _maxlen() -> int:
    try:
        return max(16, int(_setting("AUTHORIZATION_INVALIDATION_MAXLEN", 10_000)))
    except (TypeError, ValueError):
        return 10_000


def _read_block_ms() -> int:
    try:
        return max(50, int(_setting("AUTHORIZATION_INVALIDATION_READ_BLOCK_MS", 1000)))
    except (TypeError, ValueError):
        return 1000


def _reconcile_seconds() -> float:
    try:
        return max(1.0, float(_setting("AUTHORIZATION_INVALIDATION_RECONCILE_SECONDS", 5.0)))
    except (TypeError, ValueError):
        return 5.0


def _redis():
    """Return the raw Redis client backing authorization invalidation."""

    from django_redis import get_redis_connection

    return get_redis_connection(_setting("AUTHORIZATION_INVALIDATION_REDIS_ALIAS", "default"))


def _consumer_name() -> str:
    """Return this process's cursor identity, stable across reloads."""

    configured = str(_setting("AUTHORIZATION_INVALIDATION_CONSUMER", "") or "").strip()
    if configured:
        return configured
    import evennia

    if getattr(evennia, "EVENNIA_SERVER_SERVICE", None) is not None:
        return "server"
    if getattr(evennia, "EVENNIA_PORTAL_SERVICE", None) is not None:
        return "portal"
    return "standalone"


def _decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _record_error():
    try:
        from evennia.server.prometheus_metrics import (
            record_authorization_invalidation_error,
        )

        record_authorization_invalidation_error()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


def publish_generation(namespace: str, ref: str, generation: int) -> int | None:
    """Append one invalidation event; worker-safe.

    Args:
        namespace (str): ``principal`` or ``resource``.
        ref (str): The canonical fact reference that changed.
        generation (int): The new monotonically increasing generation.

    Returns:
        int or None: The global revision assigned to the event, or ``None``
        when the publish failed (the revision counter may still have advanced;
        anti-entropy reconciles the resulting gap).

    """

    try:
        client = _redis()
        revision = int(client.incr(_revision_key()))
        client.xadd(
            _stream_key(),
            {
                "r": str(revision),
                "ns": str(namespace),
                "ref": str(ref),
                "g": str(int(generation)),
            },
            maxlen=_maxlen(),
            approximate=True,
        )
        return revision
    except Exception:
        logger.log_trace("authorization invalidation publish failed")
        _record_error()
        return None


# ---------------------------------------------------------------------------
# Local application
# ---------------------------------------------------------------------------

_cursor_id = "0-0"
_applied_revision = 0
_pending_revision = 0
_reader_thread: threading.Thread | None = None
_reader_stop = threading.Event()
_reader_lock = threading.Lock()
_anti_entropy_handle = None
_consumer_identity: str | None = None


def applied_revision() -> int:
    """Return the last revision this process has fully applied."""

    return _applied_revision


def _owner_call(fn, *args):
    """Run ``fn`` on the IO owner loop, or inline when no loop is running."""

    from evennia.utils import clock

    try:
        if clock.loop_running():
            clock.call_from_thread(fn, *args, _task_kind="authz")
            return
    except Exception:
        logger.log_trace("authorization invalidation: owner handoff failed")
    fn(*args)


def apply_events(entries) -> None:
    """Apply one batch of stream entries on the owner thread.

    Args:
        entries (list): ``(entry_id, fields)`` pairs in stream order.

    """

    global _cursor_id, _applied_revision
    from . import storage

    applied = 0
    for entry_id, fields in entries:
        try:
            revision = int(_decode(fields.get(b"r") or fields.get("r") or 0))
            namespace = str(_decode(fields.get(b"ns") or fields.get("ns") or ""))
            ref = str(_decode(fields.get(b"ref") or fields.get("ref") or ""))
            generation = int(_decode(fields.get(b"g") or fields.get("g") or 0))
        except (TypeError, ValueError):
            logger.log_err("authorization invalidation: malformed event dropped")
            _cursor_id = entry_id
            continue
        if _applied_revision and revision > _applied_revision + 1:
            reconcile("gap")
        storage.apply_invalidation_event(namespace, ref, generation)
        _applied_revision = max(_applied_revision, revision)
        _cursor_id = entry_id
        applied += 1
    if applied:
        try:
            from evennia.server.prometheus_metrics import (
                record_authorization_invalidation_events,
                record_authorization_invalidation_revision,
            )

            record_authorization_invalidation_events(applied)
            record_authorization_invalidation_revision(_applied_revision)
        except Exception:
            pass
        _persist_cursor()


def reconcile(reason: str) -> None:
    """Flush local facts so a missed event cannot serve stale snapshots."""

    from . import storage

    storage.clear_authorization_caches()
    try:
        from evennia.server.prometheus_metrics import (
            record_authorization_invalidation_reconcile,
        )

        record_authorization_invalidation_reconcile(reason)
    except Exception:
        pass
    logger.log_warn(f"authorization invalidation reconciled ({reason})")


def _persist_cursor() -> None:
    """Persist the cursor off the owner thread."""

    from evennia.utils import defer

    try:
        defer.background(_persist_cursor_io, _cursor_id, _applied_revision)
    except Exception:
        try:
            _persist_cursor_io(_cursor_id, _applied_revision)
        except Exception:
            logger.log_trace("authorization invalidation cursor persist failed")


def _persist_cursor_io(cursor_id: str, revision: int) -> None:
    client = _redis()
    client.hset(
        _cursor_key(),
        _consumer_identity or _consumer_name(),
        f"{cursor_id}:{int(revision)}",
    )


def _load_cursor() -> tuple[str, int] | None:
    """Return the persisted ``(cursor_id, revision)`` for this process."""

    client = _redis()
    raw = client.hget(_cursor_key(), _consumer_identity or _consumer_name())
    if raw is None:
        return None
    value = _decode(raw)
    cursor_id, _, revision = value.rpartition(":")
    if not cursor_id or not revision:
        return None
    try:
        return cursor_id, int(revision)
    except ValueError:
        return None


def _read_batch(cursor_id: str):
    """Blocking read executed on the reader thread."""

    client = _redis()
    response = client.xread({_stream_key(): cursor_id}, count=256, block=_read_block_ms())
    entries = []
    for _stream, items in response or ():
        for entry_id, fields in items:
            entries.append((_decode(entry_id), fields))
    return entries


def _reader_thread_main() -> None:
    # The baseline runs here, on the reader thread, so starting the reader from
    # the IO owner never performs Redis I/O on the reactor.
    try:
        cursor_id, revision = _baseline_io()
    except Exception:
        logger.log_trace("authorization invalidation baseline failed")
        _record_error()
        cursor_id, revision = "0-0", 0
    _owner_call(_baseline_on_owner, cursor_id, revision)
    while not _reader_stop.is_set():
        try:
            entries = _read_batch(cursor_id)
        except Exception:
            if _reader_stop.is_set():
                return
            logger.log_trace("authorization invalidation read failed")
            _record_error()
            _reader_stop.wait(1.0)
            continue
        if entries:
            _owner_call(apply_events, entries)
            # Advance the local cursor immediately: the owner applies
            # asynchronously and duplicates are idempotent, but re-reading the
            # same entries would spin the reader.
            cursor_id = entries[-1][0]


def _read_revision() -> int:
    try:
        value = _redis().get(_revision_key())
        return int(value or 0)
    except Exception:
        _record_error()
        return -1


def _last_stream_id() -> str:
    try:
        response = _redis().xrevrange(_stream_key(), max="+", min="-", count=1)
        if response:
            return _decode(response[0][0])
    except Exception:
        _record_error()
    return "0-0"


def _anti_entropy_tick() -> None:
    """Owner-thread timer: reconcile when the global revision ran ahead."""

    global _anti_entropy_handle
    _anti_entropy_handle = None
    if not _reader_thread or not _reader_thread.is_alive():
        start()
    from evennia.utils import defer

    try:
        defer.background(_check_revision_io)
    except Exception:
        _schedule_anti_entropy()


def _check_revision_io() -> None:
    current = _read_revision()
    _owner_call(_apply_revision_check, current)


def _apply_revision_check(current: int) -> None:
    global _applied_revision, _pending_revision
    if current >= 0 and current > _applied_revision:
        if _pending_revision == current:
            # Observed twice: the reader is not about to deliver this event
            # (failed append or dead reader), so bound the staleness.
            reconcile("anti_entropy")
            _applied_revision = current
            _pending_revision = 0
        else:
            # First observation may simply race the reader; wait one interval.
            _pending_revision = current
    else:
        _pending_revision = 0
    _schedule_anti_entropy()


def _schedule_anti_entropy() -> None:
    global _anti_entropy_handle
    from evennia.utils import clock

    if _reader_stop.is_set():
        return
    try:
        _anti_entropy_handle = clock.call_later(
            _reconcile_seconds(), _anti_entropy_tick, _task_kind="authz"
        )
    except Exception:
        logger.log_trace("authorization invalidation: anti-entropy scheduling failed")


def _baseline_io() -> tuple[str, int]:
    """Return the starting ``(cursor_id, revision)`` for this process."""

    persisted = _load_cursor()
    if persisted is not None:
        cursor_id, revision = persisted
        return cursor_id, revision
    return _last_stream_id(), max(0, _read_revision())


def _baseline_on_owner(cursor_id: str, revision: int) -> None:
    global _cursor_id, _applied_revision
    _cursor_id = cursor_id
    _applied_revision = max(_applied_revision, revision)
    # A fresh process has empty caches; a restarted one may hold facts from
    # before the cursor existed. Flushing here is cheap and makes the baseline
    # unambiguous.
    reconcile("startup")
    _persist_cursor()
    _schedule_anti_entropy()


def start() -> bool:
    """Start the reader for this process once; safe to call repeatedly."""

    global _reader_thread, _consumer_identity
    if not push_enabled():
        return False
    with _reader_lock:
        if _reader_thread is not None and _reader_thread.is_alive():
            return True
        _reader_stop.clear()
        _consumer_identity = _consumer_name()
        _reader_thread = threading.Thread(
            target=_reader_thread_main,
            name="authz-invalidation-reader",
            daemon=True,
        )
        _reader_thread.start()
        return True


def stop() -> None:
    """Stop the reader and anti-entropy timer (reload/shutdown/tests)."""

    global _reader_thread, _anti_entropy_handle, _cursor_id, _applied_revision
    global _consumer_identity, _pending_revision
    _reader_stop.set()
    with _reader_lock:
        thread = _reader_thread
        _reader_thread = None
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=2.0)
    if _anti_entropy_handle is not None:
        try:
            _anti_entropy_handle.cancel()
        except Exception:
            pass
        _anti_entropy_handle = None
    _cursor_id = "0-0"
    _applied_revision = 0
    _pending_revision = 0
    _consumer_identity = None
