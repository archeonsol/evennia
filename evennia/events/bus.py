"""
Event bus: in-process listeners + optional Redis stream / PostgreSQL persistence.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from django.conf import settings

from evennia.server.amp_serde import (sanitize_event_payload,
                                      validate_event_subject)
from evennia.utils import logger

_listeners: Dict[str, List[Callable]] = {}


def _enabled() -> bool:
    return bool(getattr(settings, "EVENT_BUS_ENABLED", True))


def _backend() -> str:
    return str(getattr(settings, "EVENT_BUS_BACKEND", "memory") or "memory").lower()


def _persist_subjects() -> frozenset:
    raw = getattr(settings, "EVENT_BUS_PERSIST_SUBJECTS", None)
    if not raw:
        return frozenset()
    return frozenset(str(s).strip().lower() for s in raw)


def _should_persist(subject: str, persist: Optional[bool]) -> bool:
    # Per-call opt-out wins over any backend-driven default. Without this
    # check, ``persist=False`` was unreachable when ``EVENT_BUS_BACKEND``
    # forced postgres/both, since the emit() callsite OR'd the backend
    # check ahead of this function.
    if persist is False:
        return False
    if persist is True:
        return True
    if _backend() in ("postgres", "both"):
        return True
    return subject in _persist_subjects()


def subscribe(subject: str, callback: Callable) -> None:
    """Register an in-process listener for ``subject`` (exact match)."""
    subject = validate_event_subject(subject)
    _listeners.setdefault(subject, []).append(callback)


def emit(
    subject: str,
    payload: Optional[dict] = None,
    *,
    actor=None,
    persist: Optional[bool] = None,
) -> None:
    """
    Emit a typed engine event.

    Args:
        subject: Lowercase dotted name (e.g. ``moderation.flag``).
        payload: JSON-safe dict (sanitized).
        actor: Optional Account/Object — stored as dbref string only.
        persist: Force persist/skip; default uses ``EVENT_BUS_PERSIST_SUBJECTS``.
    """
    if not _enabled():
        return
    try:
        subject = validate_event_subject(subject)
        clean = sanitize_event_payload(payload)
    except Exception as exc:
        logger.log_warn("event_bus: rejected emit %s: %s" % (subject, exc))
        return

    actor_ref = ""
    if actor is not None:
        try:
            # Prefer stable dbref (e.g. "#42") over __str__ (often the mutable name).
            actor_ref = getattr(actor, "dbref", None) or str(actor)
        except Exception:
            actor_ref = ""

    record = {
        "subject": subject,
        "payload": clean,
        "actor_ref": actor_ref,
        "ts": time.time(),
    }

    for cb in list(_listeners.get(subject, [])):
        try:
            cb(record)
        except Exception:
            logger.log_trace("event_bus: listener failed for %s" % subject)

    backend = _backend()
    if backend in ("redis", "both"):
        _publish_redis(record)
    if _should_persist(subject, persist):
        _persist_record(record)


def _persist_record(record: dict) -> None:
    try:
        from evennia.server.models import GameEvent

        GameEvent.objects.create(
            subject=record["subject"],
            payload_json=json.dumps(record["payload"], separators=(",", ":")),
            actor_ref=record.get("actor_ref") or "",
        )
    except Exception:
        logger.log_trace("event_bus: postgres persist failed")


def _publish_redis(record: dict) -> None:
    try:
        from django_redis import get_redis_connection

        alias = getattr(settings, "EVENT_BUS_REDIS_ALIAS", "default")
        stream = getattr(settings, "EVENT_BUS_REDIS_STREAM", "evennia:events")
        r = get_redis_connection(alias)
        r.xadd(
            stream,
            {
                "subject": record["subject"],
                "payload": json.dumps(record["payload"], separators=(",", ":")),
                "actor_ref": record.get("actor_ref") or "",
                "ts": str(record.get("ts", "")),
            },
            maxlen=int(getattr(settings, "EVENT_BUS_REDIS_STREAM_MAXLEN", 100000) or 100000),
            approximate=True,
        )
    except Exception:
        logger.log_trace("event_bus: redis publish failed")
