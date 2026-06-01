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
CMD_ACCESS_CACHE_HIT_TOTAL = None
CMD_ACCESS_CACHE_MISS_TOTAL = None
LOCATION_CMDSET_CACHE_HIT_TOTAL = None
LOCATION_CMDSET_CACHE_MISS_TOTAL = None
CHANNEL_SUBSCRIBER_CACHE_HIT_TOTAL = None
CHANNEL_SUBSCRIBER_CACHE_MISS_TOTAL = None
REDIS_ATTR_CACHE_HIT_TOTAL = None
REDIS_ATTR_CACHE_MISS_TOTAL = None

_METRICS_READY = False


def _enabled() -> bool:
    return bool(getattr(settings, "ENGINE_PROMETHEUS_METRICS_ENABLED", True))


def _init_metrics() -> bool:
    """Create metrics once on the default Prometheus registry."""
    global _METRICS_READY
    global ATTR_FLUSH_TOTAL, ATTR_FLUSH_BACKENDS_TOTAL
    global ATTR_DIRTY_PENDING, ATTR_FLUSH_DURATION_SECONDS
    global CMD_ACCESS_CACHE_HIT_TOTAL, CMD_ACCESS_CACHE_MISS_TOTAL
    global LOCATION_CMDSET_CACHE_HIT_TOTAL, LOCATION_CMDSET_CACHE_MISS_TOTAL
    global CHANNEL_SUBSCRIBER_CACHE_HIT_TOTAL, CHANNEL_SUBSCRIBER_CACHE_MISS_TOTAL
    global REDIS_ATTR_CACHE_HIT_TOTAL, REDIS_ATTR_CACHE_MISS_TOTAL

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
    CMD_ACCESS_CACHE_HIT_TOTAL = Counter(
        "evennia_cmd_access_cache_hit_total",
        "cmd.access checks served from per-caller cache",
    )
    CMD_ACCESS_CACHE_MISS_TOTAL = Counter(
        "evennia_cmd_access_cache_miss_total",
        "cmd.access checks computed and stored in cache",
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


def record_cmd_access_cache_hit() -> None:
    if _init_metrics() and CMD_ACCESS_CACHE_HIT_TOTAL is not None:
        CMD_ACCESS_CACHE_HIT_TOTAL.inc()


def record_cmd_access_cache_miss() -> None:
    if _init_metrics() and CMD_ACCESS_CACHE_MISS_TOTAL is not None:
        CMD_ACCESS_CACHE_MISS_TOTAL.inc()


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
