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


def enqueue_job(job_type: str, payload: Optional[dict] = None, *, priority: int = 0) -> Optional[str]:
    """
    Enqueue a whitelisted job. Returns job id or None if disabled/rejected.
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
    else:
        _enqueue_redis(record)
    return job_id


def _enqueue_redis(record: dict) -> None:
    from django_redis import get_redis_connection

    alias = getattr(settings, "JOB_QUEUE_REDIS_ALIAS", "default")
    key = getattr(settings, "JOB_QUEUE_REDIS_KEY", "evennia:jobs:pending")
    r = get_redis_connection(alias)
    r.lpush(key, json.dumps(record, separators=(",", ":")))


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
    Drain up to ``max_jobs`` jobs in the worker thread pool.
    Call from reactor via ``defer_to_worker`` + ``schedule_on_reactor`` for follow-up.
    Returns count processed.
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
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception:
        logger.log_trace("job_queue: redis dequeue failed")
        return None


def _dequeue_db() -> Optional[dict]:
    try:
        from evennia.server.models import EngineJob

        job = (
            EngineJob.objects.filter(status="pending")
            .order_by("-priority", "created_at")
            .first()
        )
        if not job:
            return None
        job.status = "running"
        job.save(update_fields=["status"])
        return {
            "id": job.job_id,
            "type": job.job_type,
            "payload": json.loads(job.payload_json or "{}"),
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
