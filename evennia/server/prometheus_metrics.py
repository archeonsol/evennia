"""
Engine Prometheus metrics (optional ``prometheus_client`` / django-prometheus).

Counters and gauges are registered on the default registry so they appear on the
game's ``/metrics`` endpoint when django-prometheus is installed.

Disable all engine metrics with ``ENGINE_PROMETHEUS_METRICS_ENABLED = False``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from django.conf import settings

# Metric objects (None when prometheus_client is unavailable or disabled)
ATTR_FLUSH_TOTAL = None
ATTR_FLUSH_BACKENDS_TOTAL = None
ATTR_DIRTY_PENDING = None
ATTR_FLUSH_DURATION_SECONDS = None
LOCATION_CMDSET_CACHE_HIT_TOTAL = None
LOCATION_CMDSET_CACHE_MISS_TOTAL = None
CHANNEL_SUBSCRIBER_CACHE_HIT_TOTAL = None
CHANNEL_SUBSCRIBER_CACHE_MISS_TOTAL = None
REDIS_ATTR_CACHE_HIT_TOTAL = None
REDIS_ATTR_CACHE_MISS_TOTAL = None
RENDER_DELIVERY_TOTAL = None
RENDER_DELIVERY_DURATION_SECONDS = None
AUTHORIZATION_DECISIONS_TOTAL = None
AUTHORIZATION_DURATION_SECONDS = None
RUNTIME_TASKS_ACTIVE = None
RUNTIME_TASKS_TOTAL = None
RUNTIME_DB_SCOPE_CLOSES_TOTAL = None
RUNTIME_DB_UNMANAGED_TOTAL = None
IDMAPPER_FLUSH_DURATION_SECONDS = None
IDMAPPER_FLUSH_BATCHES_TOTAL = None
IDMAPPER_FLUSH_OBJECTS_TOTAL = None
IDMAPPER_FLUSH_ROW_QUERIES_TOTAL = None
IDMAPPER_FLUSH_FAILURES_TOTAL = None

_METRICS_READY = False


def _enabled() -> bool:
    return bool(getattr(settings, "ENGINE_PROMETHEUS_METRICS_ENABLED", True))


def _init_metrics() -> bool:
    """Create metrics once on the default Prometheus registry."""
    global _METRICS_READY
    global ATTR_FLUSH_TOTAL, ATTR_FLUSH_BACKENDS_TOTAL
    global ATTR_DIRTY_PENDING, ATTR_FLUSH_DURATION_SECONDS
    global LOCATION_CMDSET_CACHE_HIT_TOTAL, LOCATION_CMDSET_CACHE_MISS_TOTAL
    global CHANNEL_SUBSCRIBER_CACHE_HIT_TOTAL, CHANNEL_SUBSCRIBER_CACHE_MISS_TOTAL
    global REDIS_ATTR_CACHE_HIT_TOTAL, REDIS_ATTR_CACHE_MISS_TOTAL
    global RENDER_DELIVERY_TOTAL, RENDER_DELIVERY_DURATION_SECONDS
    global AUTHORIZATION_DECISIONS_TOTAL, AUTHORIZATION_DURATION_SECONDS
    global RUNTIME_TASKS_ACTIVE, RUNTIME_TASKS_TOTAL, RUNTIME_DB_SCOPE_CLOSES_TOTAL
    global RUNTIME_DB_UNMANAGED_TOTAL
    global IDMAPPER_FLUSH_DURATION_SECONDS, IDMAPPER_FLUSH_BATCHES_TOTAL
    global IDMAPPER_FLUSH_OBJECTS_TOTAL, IDMAPPER_FLUSH_ROW_QUERIES_TOTAL
    global IDMAPPER_FLUSH_FAILURES_TOTAL

    if _METRICS_READY:
        return ATTR_FLUSH_TOTAL is not None
    _METRICS_READY = True

    if not _enabled():
        return False

    try:
        from prometheus_client import Counter, Gauge, Histogram
    except ImportError:
        return False

    ATTR_FLUSH_TOTAL = Counter(
        "evennia_attribute_flush_total",
        "Attribute rows flushed by flush_all_dirty",
    )
    ATTR_FLUSH_BACKENDS_TOTAL = Counter(
        "evennia_attribute_flush_backends_total",
        "Dirty attribute rows flushed via JsonbAttributeBackend",
    )
    ATTR_DIRTY_PENDING = Gauge(
        "evennia_attribute_dirty_pending",
        "Unflushed attribute rows waiting for flush_all_dirty",
    )
    ATTR_FLUSH_DURATION_SECONDS = Histogram(
        "evennia_attribute_flush_duration_seconds",
        "Time spent in flush_all_dirty",
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    LOCATION_CMDSET_CACHE_HIT_TOTAL = Counter(
        "evennia_location_cmdset_cache_hit_total",
        "Location cmdset lookups served from cache",
    )
    LOCATION_CMDSET_CACHE_MISS_TOTAL = Counter(
        "evennia_location_cmdset_cache_miss_total",
        "Location cmdset lookups recomputed (cache miss or eviction)",
    )
    CHANNEL_SUBSCRIBER_CACHE_HIT_TOTAL = Counter(
        "evennia_channel_subscriber_cache_hit_total",
        "Channel subscriber lookups served from Redis cache",
    )
    CHANNEL_SUBSCRIBER_CACHE_MISS_TOTAL = Counter(
        "evennia_channel_subscriber_cache_miss_total",
        "Channel subscriber lookups rebuilt from PG (cache empty or unavailable)",
    )
    REDIS_ATTR_CACHE_HIT_TOTAL = Counter(
        "evennia_redis_attr_cache_hit_total",
        "Attribute reads served from Redis L2 cache",
    )
    REDIS_ATTR_CACHE_MISS_TOTAL = Counter(
        "evennia_redis_attr_cache_miss_total",
        "Attribute reads that fell through to PG (cache miss or unavailable)",
    )
    RENDER_DELIVERY_TOTAL = Counter(
        "evennia_render_delivery_total",
        "Universal RenderNode deliveries by protocol mode",
        ("mode",),
    )
    RENDER_DELIVERY_DURATION_SECONDS = Histogram(
        "evennia_render_delivery_duration_seconds",
        "Time spent resolving and dispatching one viewer RenderNode",
        ("mode",),
        buckets=(0.0001, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1),
    )
    AUTHORIZATION_DECISIONS_TOTAL = Counter(
        "evennia_authorization_decisions_total",
        "Capability authorization decisions by resource kind and result",
        ("resource_kind", "result", "reason"),
    )
    AUTHORIZATION_DURATION_SECONDS = Histogram(
        "evennia_authorization_duration_seconds",
        "Time spent evaluating one structured authorization decision",
        ("resource_kind",),
        buckets=(0.00005, 0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01),
    )
    RUNTIME_TASKS_ACTIVE = Gauge(
        "evennia_runtime_tasks_active",
        "Detached runtime roots currently executing",
        ("task_kind",),
    )
    RUNTIME_TASKS_TOTAL = Counter(
        "evennia_runtime_tasks_total",
        "Detached runtime roots by terminal result",
        ("task_kind", "result"),
    )
    RUNTIME_DB_SCOPE_CLOSES_TOTAL = Counter(
        "evennia_runtime_db_scope_closes_total",
        "Runtime roots that released an opened Django connection",
        ("task_kind",),
    )
    RUNTIME_DB_UNMANAGED_TOTAL = Counter(
        "evennia_runtime_db_unmanaged_total",
        "Django connections opened by unmanaged asyncio contexts",
    )
    IDMAPPER_FLUSH_DURATION_SECONDS = Histogram(
        "evennia_idmapper_flush_duration_seconds",
        "Wall time spent completing one automatic idmapper pressure sweep",
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )
    IDMAPPER_FLUSH_BATCHES_TOTAL = Counter(
        "evennia_idmapper_flush_batches_total",
        "Bounded reactor turns used by automatic idmapper pressure sweeps",
    )
    IDMAPPER_FLUSH_OBJECTS_TOTAL = Counter(
        "evennia_idmapper_flush_objects_total",
        "Idmapper objects processed by automatic pressure sweeps",
        ("outcome",),
    )
    IDMAPPER_FLUSH_ROW_QUERIES_TOTAL = Counter(
        "evennia_idmapper_flush_row_queries_total",
        "Bulk backing-row existence queries used by idmapper pressure sweeps",
    )
    IDMAPPER_FLUSH_FAILURES_TOTAL = Counter(
        "evennia_idmapper_flush_failures_total",
        "Automatic idmapper pressure sweeps aborted by an error",
    )
    return True


def record_attribute_flush(stats: dict, *, duration_seconds: Optional[float] = None) -> None:
    """
    Update Prometheus counters/gauge/histogram after ``flush_all_dirty``.
    """
    if not stats or not _init_metrics():
        return

    total = int(stats.get("total") or 0)
    backends = int(stats.get("backends") or 0)
    pending = int(stats.get("pending") or 0)

    if ATTR_DIRTY_PENDING is not None:
        ATTR_DIRTY_PENDING.set(pending)

    if total and ATTR_FLUSH_TOTAL is not None:
        ATTR_FLUSH_TOTAL.inc(total)
    if backends and ATTR_FLUSH_BACKENDS_TOTAL is not None:
        ATTR_FLUSH_BACKENDS_TOTAL.inc(backends)
    if duration_seconds is not None and ATTR_FLUSH_DURATION_SECONDS is not None:
        ATTR_FLUSH_DURATION_SECONDS.observe(duration_seconds)


def record_idmapper_flush(
    stats: dict, *, duration_seconds: Optional[float] = None, failed: bool = False
) -> None:
    """Record one completed or aborted automatic idmapper pressure sweep."""
    if not stats or not _init_metrics():
        return
    if duration_seconds is not None and IDMAPPER_FLUSH_DURATION_SECONDS is not None:
        IDMAPPER_FLUSH_DURATION_SECONDS.observe(max(0.0, float(duration_seconds)))
    if IDMAPPER_FLUSH_BATCHES_TOTAL is not None:
        IDMAPPER_FLUSH_BATCHES_TOTAL.inc(int(stats.get("batches") or 0))
    if IDMAPPER_FLUSH_OBJECTS_TOTAL is not None:
        for outcome in ("retained", "evicted", "stale"):
            count = int(stats.get(outcome) or 0)
            if count:
                IDMAPPER_FLUSH_OBJECTS_TOTAL.labels(outcome=outcome).inc(count)
    if IDMAPPER_FLUSH_ROW_QUERIES_TOTAL is not None:
        IDMAPPER_FLUSH_ROW_QUERIES_TOTAL.inc(int(stats.get("row_queries") or 0))
    if failed and IDMAPPER_FLUSH_FAILURES_TOTAL is not None:
        IDMAPPER_FLUSH_FAILURES_TOTAL.inc()


def record_location_cmdset_cache_hit() -> None:
    if _init_metrics() and LOCATION_CMDSET_CACHE_HIT_TOTAL is not None:
        LOCATION_CMDSET_CACHE_HIT_TOTAL.inc()


def record_location_cmdset_cache_miss() -> None:
    if _init_metrics() and LOCATION_CMDSET_CACHE_MISS_TOTAL is not None:
        LOCATION_CMDSET_CACHE_MISS_TOTAL.inc()


def record_channel_subscriber_cache_hit() -> None:
    if _init_metrics() and CHANNEL_SUBSCRIBER_CACHE_HIT_TOTAL is not None:
        CHANNEL_SUBSCRIBER_CACHE_HIT_TOTAL.inc()


def record_channel_subscriber_cache_miss() -> None:
    if _init_metrics() and CHANNEL_SUBSCRIBER_CACHE_MISS_TOTAL is not None:
        CHANNEL_SUBSCRIBER_CACHE_MISS_TOTAL.inc()


def record_redis_attr_cache_hit() -> None:
    if _init_metrics() and REDIS_ATTR_CACHE_HIT_TOTAL is not None:
        REDIS_ATTR_CACHE_HIT_TOTAL.inc()


def record_redis_attr_cache_miss() -> None:
    if _init_metrics() and REDIS_ATTR_CACHE_MISS_TOTAL is not None:
        REDIS_ATTR_CACHE_MISS_TOTAL.inc()


def observe_attribute_dirty_pending(pending: int) -> None:
    if _init_metrics() and ATTR_DIRTY_PENDING is not None:
        ATTR_DIRTY_PENDING.set(int(pending))


def record_render_delivery(mode: str, duration_seconds: float) -> None:
    """Record one low-cardinality universal-render delivery."""
    if not _init_metrics():
        return
    normalized = mode if mode in {"text", "structured", "mixed"} else "text"
    if RENDER_DELIVERY_TOTAL is not None:
        RENDER_DELIVERY_TOTAL.labels(mode=normalized).inc()
    if RENDER_DELIVERY_DURATION_SECONDS is not None:
        RENDER_DELIVERY_DURATION_SECONDS.labels(mode=normalized).observe(
            max(0.0, float(duration_seconds))
        )


def record_authorization_decision(
    resource_kind: str, allowed: bool, duration_seconds: float, reason: str = "unknown"
) -> None:
    """Record one low-cardinality structured authorization evaluation."""

    if not _init_metrics():
        return
    kind = str(resource_kind or "unknown")[:32]
    result = "allow" if allowed else "deny"
    reason = str(reason or "unknown")[:48]
    if AUTHORIZATION_DECISIONS_TOTAL is not None:
        AUTHORIZATION_DECISIONS_TOTAL.labels(resource_kind=kind, result=result, reason=reason).inc()
    if AUTHORIZATION_DURATION_SECONDS is not None:
        AUTHORIZATION_DURATION_SECONDS.labels(resource_kind=kind).observe(
            max(0.0, float(duration_seconds))
        )


def record_runtime_task(task_kind: str, event: str, *, had_connection: bool = False) -> None:
    """Record one bounded detached-root lifecycle transition."""

    if not _init_metrics():
        return
    kind = str(task_kind or "generic")[:24]
    if event == "started":
        if RUNTIME_TASKS_ACTIVE is not None:
            RUNTIME_TASKS_ACTIVE.labels(task_kind=kind).inc()
        return
    if RUNTIME_TASKS_ACTIVE is not None:
        RUNTIME_TASKS_ACTIVE.labels(task_kind=kind).dec()
    if RUNTIME_TASKS_TOTAL is not None:
        result = event if event in {"completed", "failed", "cancelled"} else "failed"
        RUNTIME_TASKS_TOTAL.labels(task_kind=kind, result=result).inc()
    if had_connection and RUNTIME_DB_SCOPE_CLOSES_TOTAL is not None:
        RUNTIME_DB_SCOPE_CLOSES_TOTAL.labels(task_kind=kind).inc()


def record_unmanaged_db_connection() -> None:
    """Record one ORM connection opened outside a supervised root."""

    if _init_metrics() and RUNTIME_DB_UNMANAGED_TOTAL is not None:
        RUNTIME_DB_UNMANAGED_TOTAL.inc()
