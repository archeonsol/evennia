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


def _max_attempts() -> int:
    return int(getattr(settings, "JOB_QUEUE_MAX_ATTEMPTS", 5) or 5)


def _lease_seconds() -> float:
    return float(getattr(settings, "JOB_QUEUE_LEASE_SECONDS", 60) or 60)


def _backoff_seconds(attempts: int) -> float:
    """Exponential backoff (base 2) for the *next* attempt, bounded."""
    base = float(getattr(settings, "JOB_QUEUE_BACKOFF_BASE", 2.0) or 2.0)
    cap = float(getattr(settings, "JOB_QUEUE_BACKOFF_CAP_SECONDS", 300.0) or 300.0)
    return min(cap, base ** max(0, attempts))


def enqueue_job(
    job_type: str,
    payload: Optional[dict] = None,
    *,
    priority: int = 0,
    idempotency_key: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> Optional[str]:
    """
    Enqueue a whitelisted job. Returns the job id, or None if the queue is
    disabled, the job is rejected, or the backend is unavailable (the job is
    dropped, not deferred).

    Args:
        job_type: Registered job type (see ``JOB_QUEUE_REGISTRY``).
        payload: Sanitized dict handed to the handler.
        priority: Higher runs first.
        idempotency_key: When set and the Postgres backend is active, a second
            enqueue with the same key is a no-op and returns the existing job
            id — safe to retry an enqueue without duplicating work. (The Redis
            backend does not dedupe; the key is stored in the record only.)
        max_attempts: Override the retry budget for this job.
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
        "attempts": 0,
        "max_attempts": int(max_attempts) if max_attempts else _max_attempts(),
        "idempotency_key": idempotency_key,
    }
    backend = _backend()
    if backend == "postgres":
        return _enqueue_db(record)
    if not _enqueue_redis(record):
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


def _redis_keys():
    """(pending, processing, dead) list keys for this worker."""
    key = getattr(settings, "JOB_QUEUE_REDIS_KEY", "evennia:jobs:pending")
    worker = str(getattr(settings, "SERVER_WORKER_ID", "0"))
    return key, f"{key}:processing:{worker}", f"{key}:dead"


def _enqueue_redis(record: dict) -> Optional[str]:
    """Push a job record to Redis. Returns the job id, or None if dropped."""
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        key, _processing, _dead = _redis_keys()
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


def _enqueue_db(record: dict) -> Optional[str]:
    from django.utils import timezone

    from evennia.server.models import EngineJob

    common = dict(
        job_type=record["type"],
        payload_json=json.dumps(record["payload"], separators=(",", ":")),
        priority=record["priority"],
        status="pending",
        attempts=0,
        max_attempts=record["max_attempts"],
        available_at=timezone.now(),
    )
    key = record.get("idempotency_key")
    if key:
        # Idempotent enqueue: a duplicate key returns the existing job id and
        # enqueues nothing new.
        obj, created = EngineJob.objects.get_or_create(
            idempotency_key=key, defaults={"job_id": record["id"], **common}
        )
        return obj.job_id
    EngineJob.objects.create(job_id=record["id"], **common)
    return record["id"]


def _deferred_or_awaitable(result):
    """Return a callable ``add_done(on_ok, on_err)`` if ``result`` needs
    awaiting (asyncio awaitable or a Twisted Deferred), else None."""
    import inspect

    if inspect.isawaitable(result):

        def _add(on_ok, on_err):
            from evennia.utils import clock

            async def _runner():
                try:
                    await result
                except Exception as exc:
                    on_err(exc)
                else:
                    on_ok()

            clock.run_coroutine(_runner())

        return _add
    if hasattr(result, "addCallbacks") or hasattr(result, "addBoth"):

        def _add(on_ok, on_err):
            result.addCallbacks(lambda _res: on_ok(), lambda failure: on_err(failure))

        return _add
    return None


def process_pending_jobs(*, max_jobs: int = 10) -> int:
    """
    Lease and run up to ``max_jobs`` due jobs, honoring the durable contract.

    Each job is *leased* (not destructively removed): it is marked complete only
    after its handler finishes successfully, and an awaitable/Deferred handler's
    completion is awaited before the job is acked — so a crash mid-run leaves the
    job reclaimable rather than lost, and slow async work is not counted done
    early. On handler failure the job is retried with exponential backoff until
    ``max_attempts``, then dead-lettered.

    Call this on the reactor thread (e.g. the global tick / maintenance loop,
    gated by ``JOB_QUEUE_DRAIN_EVERY_N_TICKS``). Job handlers run on the reactor
    thread and may therefore touch game objects freely. Do NOT drain this from a
    worker thread: the typeclass + idmapper layer is not concurrency-safe.

    Returns the count of jobs *dispatched* this call (leased and handed to a
    handler); asynchronous handlers may still be completing after return.
    """
    if not _enabled():
        return 0
    dispatched = 0
    for _ in range(max(1, int(max_jobs))):
        record = _dequeue_one()
        if not record:
            break
        dispatched += 1
        _run_leased(record)
    return dispatched


def _run_leased(record: dict) -> None:
    """Run one already-leased job; complete or fail per the contract."""
    try:
        fn = _resolve_callable(record["type"])
        payload = record.get("payload") or {}
        result = fn(payload)
    except Exception as exc:
        logger.log_trace("job_queue: job %s handler raised" % record.get("id"))
        _fail_job(record, exc)
        return
    add_done = _deferred_or_awaitable(result)
    if add_done is None:
        _complete_job(record)
        return
    # Async handler: ack only when the real work resolves.
    add_done(lambda: _complete_job(record), lambda exc: _fail_job(record, exc))


def _complete_job(record: dict) -> None:
    if _backend() == "postgres":
        _complete_db(record)
    else:
        _complete_redis(record)


def _fail_job(record: dict, exc) -> None:
    logger.log_err("job_queue: job %s failed: %s" % (record.get("id"), exc))
    if _backend() == "postgres":
        _retry_or_dead_db(record)
    else:
        _retry_or_dead_redis(record)


def _dequeue_one() -> Optional[dict]:
    if _backend() == "postgres":
        return _dequeue_db()
    return _dequeue_redis()


def _dequeue_redis() -> Optional[dict]:
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        key, processing, _dead = _redis_keys()
        r = get_redis_connection(alias)
        # Atomically move the job to a per-worker processing list so a crash
        # after the pop still leaves the job recoverable (reclaim_jobs moves it
        # back). Plain RPOP would lose it here.
        raw = r.rpoplpush(key, processing)
        _on_redis_alive()
        if not raw:
            return None
        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        record = json.loads(raw_str)
        # keep the exact serialized form so completion/retry can LREM it
        record["_raw"] = raw_str
        record["attempts"] = int(record.get("attempts", 0)) + 1
        return record
    except Exception as exc:
        if _is_redis_unavailable(exc):
            _on_redis_down()
            return None
        logger.log_trace("job_queue: redis dequeue failed")
        return None


def _complete_redis(record: dict) -> None:
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        _key, processing, _dead = _redis_keys()
        r = get_redis_connection(alias)
        if record.get("_raw") is not None:
            r.lrem(processing, 1, record["_raw"])
    except Exception:
        logger.log_trace("job_queue: redis complete failed")


def _retry_or_dead_redis(record: dict) -> None:
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        key, processing, dead = _redis_keys()
        r = get_redis_connection(alias)
        raw = record.get("_raw")
        if raw is not None:
            r.lrem(processing, 1, raw)
        attempts = int(record.get("attempts", 1))
        max_attempts = int(record.get("max_attempts", _max_attempts()))
        payload = {k: v for k, v in record.items() if k != "_raw"}
        payload["attempts"] = attempts
        blob = json.dumps(payload, separators=(",", ":"))
        if attempts >= max_attempts:
            r.lpush(dead, blob)  # dead-letter
            logger.log_err("job_queue: job %s dead-lettered after %d attempts" % (record.get("id"), attempts))
        else:
            # Redis lists have no delay; requeue immediately for another attempt.
            r.lpush(key, blob)
    except Exception:
        logger.log_trace("job_queue: redis retry/dead failed")


def _dequeue_db() -> Optional[dict]:
    try:
        from django.db import transaction
        from django.db.models import Q
        from django.utils import timezone

        from evennia.server.models import EngineJob

        now = timezone.now()
        lease_delta = timezone.timedelta(seconds=_lease_seconds())
        with transaction.atomic():
            # Eligible: pending-and-available, or a leased job whose lease
            # expired (its worker died mid-run) — reclaimed here.
            eligible = (Q(status="pending") | Q(status="leased", lease_until__lt=now)) & (
                Q(available_at__isnull=True) | Q(available_at__lte=now)
            )
            qs = EngineJob.objects.filter(eligible).order_by("-priority", "created_at")
            try:
                job = qs.select_for_update(skip_locked=True).first()
            except Exception:
                job = qs.first()
            if not job:
                return None
            if job.status not in ("pending", "leased"):
                return None
            job.status = "leased"
            job.attempts += 1
            job.lease_until = now + lease_delta
            job.save(update_fields=["status", "attempts", "lease_until", "updated_at"])
            return {
                "id": job.job_id,
                "type": job.job_type,
                "payload": json.loads(job.payload_json or "{}"),
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
            }
    except Exception:
        logger.log_trace("job_queue: db dequeue failed")
        return None


def _complete_db(record: dict) -> None:
    try:
        from django.utils import timezone

        from evennia.server.models import EngineJob

        EngineJob.objects.filter(job_id=record.get("id")).update(
            status="completed", completed_at=timezone.now(), lease_until=None
        )
    except Exception:
        logger.log_trace("job_queue: db complete failed")


def _retry_or_dead_db(record: dict) -> None:
    try:
        from django.utils import timezone

        from evennia.server.models import EngineJob

        attempts = int(record.get("attempts", 1))
        max_attempts = int(record.get("max_attempts", _max_attempts()))
        now = timezone.now()
        if attempts >= max_attempts:
            EngineJob.objects.filter(job_id=record.get("id")).update(
                status="dead", lease_until=None, last_error="max attempts reached"
            )
            logger.log_err(
                "job_queue: job %s dead-lettered after %d attempts" % (record.get("id"), attempts)
            )
        else:
            EngineJob.objects.filter(job_id=record.get("id")).update(
                status="pending",
                lease_until=None,
                available_at=now + timezone.timedelta(seconds=_backoff_seconds(attempts)),
            )
    except Exception:
        logger.log_trace("job_queue: db retry/dead failed")


def reclaim_jobs() -> int:
    """
    Reclaim jobs left in-flight by a crashed worker. Call at server start.

    Postgres: leased rows whose lease has expired are already reclaimed lazily
    by :func:`_dequeue_db`; this proactively resets any stuck ``leased`` rows
    (including ones with a null lease) to ``pending`` so they are retried.
    Redis: the per-worker processing list is drained back onto the pending list.

    Returns the number of jobs reclaimed.
    """
    if not _enabled():
        return 0
    if _backend() == "postgres":
        try:
            from django.db.models import Q
            from django.utils import timezone

            from evennia.server.models import EngineJob

            now = timezone.now()
            n = EngineJob.objects.filter(
                Q(status="leased") & (Q(lease_until__isnull=True) | Q(lease_until__lt=now))
            ).update(status="pending", lease_until=None)
            if n:
                logger.log_info("job_queue: reclaimed %d stuck leased job(s)" % n)
            return n
        except Exception:
            logger.log_trace("job_queue: db reclaim failed")
            return 0
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
        key, processing, _dead = _redis_keys()
        r = get_redis_connection(alias)
        reclaimed = 0
        while r.rpoplpush(processing, key) is not None:
            reclaimed += 1
        if reclaimed:
            logger.log_info("job_queue: reclaimed %d in-flight job(s) from processing" % reclaimed)
        return reclaimed
    except Exception as exc:
        if not _is_redis_unavailable(exc):
            logger.log_trace("job_queue: redis reclaim failed")
        return 0
