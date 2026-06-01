"""
Metrics and logging helpers for attribute write-behind flush.
"""

from __future__ import annotations

from typing import Optional

from django.conf import settings

from evennia.utils import logger


def record_attribute_flush_stats(stats: dict, *, duration_seconds: Optional[float] = None) -> None:
    """
    Record flush stats to Prometheus and leave log lines to ``maybe_log_flush_metrics``.
    """
    if not stats:
        return
    try:
        from evennia.server.prometheus_metrics import record_attribute_flush

        record_attribute_flush(stats, duration_seconds=duration_seconds)
    except Exception:
        pass


def maybe_warn_pending_dirty(stats: dict, tick_count: int) -> None:
    """
    Log a warning when unflushed attribute backlog exceeds
    ``ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD`` (0 = disabled).
    """
    if not stats:
        return
    threshold = int(getattr(settings, "ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD", 0) or 0)
    if threshold <= 0:
        return
    pending = int(stats.get("pending", 0) or 0)
    if pending < threshold:
        return
    logger.log_warn(
        "attribute_flush backlog pending=%s threshold=%s tick=%s "
        "(tick loop may be stalled; check flush_all_dirty and handlers)"
        % (pending, threshold, tick_count)
    )


def maybe_log_flush_metrics(stats: dict, tick_count: int) -> None:
    """
    Log flush batch sizes every ``ATTRIBUTE_FLUSH_METRICS_EVERY_N_TICKS`` ticks.
    """
    if not stats:
        return
    every_n = int(getattr(settings, "ATTRIBUTE_FLUSH_METRICS_EVERY_N_TICKS", 0) or 0)
    if every_n <= 0 or (tick_count % every_n) != 0:
        return
    if not stats.get("total"):
        return
    logger.log_info(
        "attribute_flush tick=%s backends=%s total=%s pending=%s"
        % (
            tick_count,
            stats.get("backends", 0),
            stats.get("total", 0),
            stats.get("pending", 0),
        )
    )
