"""
Secure job queue: registry-only callables, sanitized payloads, Redis or DB backend.
"""

from __future__ import annotations

import importlib
import json
import time
import uuid
from typing import Any, Callable, Dict, Optional

from django.conf import settings

from evennia.server.amp_serde import sanitize_event_payload
from evennia.utils import logger

_REGISTRY: Dict[str, str] = {}


def _enabled() -> bool:
    return bool(getattr(settings, "JOB_QUEUE_ENABLED", True))


def _backend() -> str:
    return str(getattr(settings, "JOB_QUEUE_BACKEND", "redis") or "redis").lower()


def _load_registry() -> Dict[str, str]:
    global _REGISTRY
    if not _REGISTRY:
        reg = getattr(settings, "JOB_QUEUE_REGISTRY", None) or {}
        if isinstance(reg, dict):
            _REGISTRY = {str(k): str(v) for k, v in reg.items()}
    return _REGISTRY


def register_job_type(job_type: str, dotted_path: str) -> None:
    """Runtime registration (e.g. game ``at_server_init``)."""
    _REGISTRY[str(job_type)] = str(dotted_path)


def _resolve_callable(job_type: str) -> Callable:
    reg = _load_registry()
    if job_type not in reg:
        raise ValueError("job_type not in JOB_QUEUE_REGISTRY: %s" % job_type)
    path = reg[job_type]
    if not path or ".." in path:
        raise ValueError("invalid job dotted path")
    module_path, _, func_name = path.rpartition(".")
    if not module_path or not func_name:
        raise ValueError("JOB_QUEUE_REGISTRY path must be module.function")
    mod = importlib.import_module(module_path)
    fn = getattr(mod, func_name, None)
    if not callable(fn):
        raise ValueError("JOB_QUEUE_REGISTRY target not callable: %s" % path)
    return fn


def enqueue_job(
    job_type: str, payload: Optional[dict] = None, *, priority: int = 0
) -> Optional[str]:
    """
    Enqueue a whitelisted job. Returns the job id, or None if the queue is
    disabled, the job is rejected, or the backend is unavailable (the job is
    dropped, not deferred).
    """
    if not _enabled():
        return None
    job_type = (job_type or "").strip()
    if not job_type or len(job_type) > 64:
        logger.log_warn("job_queue: invalid job_type %r" % job_type)
        return None
    try:
        _resolve_callable(job_type)
        clean = sanitize_event_payload(payload)
    except Exception as exc:
        logger.log_warn("job_queue: rejected %s: %s" % (job_type, exc))
        return None

    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "type": job_type,
        "payload": clean,
        "priority": int(priority),
        "created": time.time(),
    }
    backend = _backend()
    if backend == "postgres":
        _enqueue_db(record)
    elif not _enqueue_redis(record):
        return None
    return job_id


# Redis is an optional backend: it may be unreachable (server down / network
# blip) or not installed at all, yet "redis" is the default backend. The dequeue
# side is polled on a ticker, so logging a traceback per poll would flood the log.
#
# Availability is tracked as a small state machine so the log stays calm but
# honest. Until Redis is ever seen alive (not installed / down since boot) an
# outage is complained about exactly once. Once Redis has been live and is then
# lost, that genuine outage is logged on first loss and then re-stated at most
# once per `_REDIS_DOWN_LOG_EVERY` seconds. Recovery is detected by a real probe
# (a successful PING / round-trip), not a timer, and logged once. Genuine per-call
# bugs are never collapsed this way; they keep a full traceback.
_REDIS_DOWN_LOG_EVERY = 600.0  # seconds between repeated "still down" heartbeat lines
_redis_seen_alive = False  # has Redis ever responded since this process started?
_redis_down_count = 0  # consecutive failed checks in the current outage
_redis_last_down_log = 0.0  # time.monotonic() of the last "still down" line


def _is_redis_unavailable(exc: BaseException) -> bool:
    """
    True if ``exc`` means the Redis backend is unavailable (server unreachable or
    the optional ``redis``/``django_redis`` packages not installed), as opposed to
    a genuine bug.

    The connection errors are matched structurally (by class module/name) so the
    jobs module needn't import ``redis`` itself.
    """
    if isinstance(exc, ImportError):
        return True
    for cls in type(exc).__mro__:
        if getattr(cls, "__module__", "").startswith("redis") and getattr(cls, "__name__", "") in (
            "ConnectionError",
            "TimeoutError",
            "BusyLoadingError",
        ):
            return True
    return False


def _on_redis_alive() -> None:
    """Mark Redis reachable, logging first contact and any recovery exactly once."""
    global _redis_seen_alive, _redis_down_count, _redis_last_down_log
    if not _redis_seen_alive:
        logger.log_info("job_queue: Redis backend is live")
    elif _redis_down_count:
        logger.log_info(
            "job_queue: Redis backend recovered after %d failed check(s)" % _redis_down_count
        )
    _redis_seen_alive = True
    _redis_down_count = 0
    _redis_last_down_log = 0.0


def _on_redis_down() -> None:
    """Record an unavailable Redis backend with a calm, bounded log cadence."""
    global _redis_down_count, _redis_last_down_log
    _redis_down_count += 1
    if not _redis_seen_alive:
        # Never came up (not installed / down since boot): complain just once.
        if _redis_down_count == 1:
            logger.log_warn(
                "job_queue: Redis backend unavailable; the job queue is idle until it "
                "becomes reachable"
            )
        return
    # Redis was live and has been lost: keep a real outage visible without flooding.
    now = time.monotonic()
    if _redis_down_count == 1 or (now - _redis_last_down_log) >= _REDIS_DOWN_LOG_EVERY:
        _redis_last_down_log = now
        logger.log_warn(
            "job_queue: Redis backend still down (%d failed checks); retrying" % _redis_down_count
        )


def _redis_ping() -> bool:
    """Active liveness probe: True if Redis answers, False if it is unavailable."""
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        get_redis_connection(alias).ping()
    except Exception as exc:
        if _is_redis_unavailable(exc):
            return False
        raise
    return True


def check_redis_backend() -> bool:
    """
    Probe the Redis job-queue backend and update its availability logging.

    Safe to call at server start and from the server maintenance loop. Performs a
    real ``PING``, drives the unavailable/recovered log cadence, and returns True
    if Redis is reachable. Returns True without probing when the queue is disabled
    or a non-redis backend is configured.
    """
    if not _enabled() or _backend() != "redis":
        return True
    if _redis_ping():
        _on_redis_alive()
        return True
    _on_redis_down()
    return False


def _enqueue_redis(record: dict) -> bool:
    """Push a job record to Redis. True if accepted, False if the job was dropped."""
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        key = getattr(settings, "JOB_QUEUE_REDIS_KEY", "evennia:jobs:pending")
        r = get_redis_connection(alias)
        r.lpush(key, json.dumps(record, separators=(",", ":")))
    except Exception as exc:
        # An unavailable backend means the queue can't accept the job: complain
        # once and drop it rather than raising a fresh traceback into game code on
        # every enqueue during an outage. Genuine bugs still propagate.
        if _is_redis_unavailable(exc):
            _on_redis_down()
            return False
        raise
    _on_redis_alive()
    return True


def _enqueue_db(record: dict) -> None:
    from evennia.server.models import EngineJob

    EngineJob.objects.create(
        job_id=record["id"],
        job_type=record["type"],
        payload_json=json.dumps(record["payload"], separators=(",", ":")),
        priority=record["priority"],
        status="pending",
    )


def process_pending_jobs(*, max_jobs: int = 10) -> int:
    """
    Drain up to ``max_jobs`` pending jobs, running each handler inline.

    Call this on the reactor thread (e.g. the global tick / maintenance loop,
    gated by ``JOB_QUEUE_DRAIN_EVERY_N_TICKS``). Job handlers run on the reactor
    thread and may therefore touch game objects freely. Do NOT drain this from a
    worker thread (``evennia.utils.defer.in_thread`` / ``deferToThread``): the
    typeclass + idmapper layer is not concurrency-safe, so a handler touching
    ``.db`` / typeclasses / ``obj.msg`` off the reactor would race game state.

    Keep handlers light. A handler that needs blocking I/O (HTTP webhook, search
    rebuild) must offload just that part via ``evennia.utils.defer.in_thread`` /
    ``background`` and deliver the result back in the reactor-thread callback,
    rather than blocking here. Dequeue itself is plain ORM and is safe on either
    thread.

    Returns the count of jobs processed.
    """
    if not _enabled():
        return 0
    processed = 0
    for _ in range(max(1, int(max_jobs))):
        record = _dequeue_one()
        if not record:
            break
        try:
            fn = _resolve_callable(record["type"])
            payload = record.get("payload") or {}
            fn(payload)
            processed += 1
        except Exception:
            logger.log_trace("job_queue: job %s failed" % record.get("id"))
            _mark_failed(record)
    return processed


def _dequeue_one() -> Optional[dict]:
    if _backend() == "postgres":
        return _dequeue_db()
    return _dequeue_redis()


def _dequeue_redis() -> Optional[dict]:
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        key = getattr(settings, "JOB_QUEUE_REDIS_KEY", "evennia:jobs:pending")
        r = get_redis_connection(alias)
        raw = r.rpop(key)
        _on_redis_alive()
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as exc:
        # An unavailable backend on a polled dequeue feeds the availability state
        # machine (complained about once, then bounded); only genuinely unexpected
        # errors get a full traceback.
        if _is_redis_unavailable(exc):
            _on_redis_down()
            return None
        logger.log_trace("job_queue: redis dequeue failed")
        return None


def _dequeue_db() -> Optional[dict]:
    try:
        from django.db import transaction

        from evennia.server.models import EngineJob

        # Atomic claim: SELECT FOR UPDATE SKIP LOCKED lets one of N
        # concurrent workers win a row while the rest see the next
        # pending job. Falls back to an unlocked SELECT on SQLite where
        # row-level locking isn't supported; for SQLite a single worker
        # is assumed (the default Evennia deployment).
        with transaction.atomic():
            qs = EngineJob.objects.filter(status="pending").order_by("-priority", "created_at")
            try:
                job = qs.select_for_update(skip_locked=True).first()
            except Exception:
                job = qs.first()
            if not job:
                return None
            # Re-check under the row lock; a peer that won the row would
            # have flipped status away from 'pending'.
            if job.status != "pending":
                return None
            job.status = "running"
            job.save(update_fields=["status"])
            payload = json.loads(job.payload_json or "{}")
            return {
                "id": job.job_id,
                "type": job.job_type,
                "payload": payload,
            }
    except Exception:
        logger.log_trace("job_queue: db dequeue failed")
        return None


def _mark_failed(record: dict) -> None:
    if _backend() != "postgres":
        return
    try:
        from evennia.server.models import EngineJob

        EngineJob.objects.filter(job_id=record.get("id")).update(status="failed")
    except Exception:
        pass
