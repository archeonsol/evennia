"""What the server is doing right now.

The metrics already existed -- twenty-two of them, instrumented across the
attribute flush, three cache pairs, render delivery, authorization decisions,
supervised task roots, and the idmapper flush. Nothing could see them without
scraping the Prometheus endpoint, which means in practice nobody looked.

Two of the numbers here are correctness alarms rather than performance
readings, and the panel says so rather than lining them up with the rest:
unmanaged database access from a supervised task, and idmapper flush failures.
A rate that should be zero is a different kind of fact from a rate that should
be low.

The cache section is the one with teeth. ``core-beliefs.md`` requires every
cache to document what fills it, what invalidates it, and the bound on a stale
read, and records that seven have been added and none removed. Showing hit rate
beside each contract is how the eighth gets refused, or an existing one gets
deleted.

"""

from __future__ import annotations

from evennia.console.registry import Panel

#: Metrics whose correct value is zero. Presented as alarms, not as readings.
ALARM_METRICS = {
    "evennia_runtime_db_unmanaged_total": (
        "A supervised task reached the database outside a managed scope."
    ),
    "evennia_idmapper_flush_failures_total": "An idmapper flush did not complete.",
}

#: The caches the engine runs, with the invalidation contract each one owes.
#: Listed here rather than derived, because the contract is prose that lives
#: with the decision, and a cache with no entry has not documented itself.
CACHE_CONTRACTS = (
    {
        "name": "location cmdset",
        "hit": "evennia_location_cmdset_cache_hit_total",
        "miss": "evennia_location_cmdset_cache_miss_total",
        "invalidation": "Generation counter bumped on cmdset change.",
    },
    {
        "name": "channel subscribers",
        "hit": "evennia_channel_subscriber_cache_hit_total",
        "miss": "evennia_channel_subscriber_cache_miss_total",
        "invalidation": "Redis SET index; PostgreSQL M2M remains the source of truth.",
    },
    {
        "name": "redis attributes",
        "hit": "evennia_redis_attr_cache_hit_total",
        "miss": "evennia_redis_attr_cache_miss_total",
        "invalidation": "Write-behind flush; bounded by ATTRIBUTE_FLUSH_INTERVAL.",
    },
)


def _samples():
    """Return current engine metric samples keyed by name.

    Returns:
        tuple: ``(available, {name: value}, reason)``.
    """

    try:
        from prometheus_client import REGISTRY
    except ImportError:
        return False, {}, "prometheus_client is not installed, so no metrics are collected."

    values = {}
    try:
        for metric in REGISTRY.collect():
            if not metric.name.startswith("evennia_"):
                continue
            for sample in metric.samples:
                values[sample.name] = values.get(sample.name, 0.0) + float(sample.value)
    except Exception:  # noqa: BLE001 - a reading must never break the page
        return False, {}, "The metric registry could not be read."
    return True, values, ""


class RuntimePanel(Panel):
    """Metrics, caches, scheduled systems, and supervised tasks."""

    key = "runtime"
    label = "Runtime"
    description = "Metrics, cache hit rates, scheduled systems, and task roots."
    columns = ("metric", "value")
    needs_io = False

    def rows(self, ctx):
        """Return the current runtime picture.

        Returns:
            dict: Alarms first, then caches with their contracts, then every
            remaining metric, then the scheduler and task roots.
        """

        available, values, reason = _samples()
        return {
            "metrics_available": available,
            "reason": reason,
            "alarms": self._alarms(values),
            "caches": self._caches(values),
            "metrics": sorted(
                ({"name": name, "value": value} for name, value in values.items()),
                key=lambda row: row["name"],
            ),
            "systems": self._systems(),
            "tasks": self._tasks(values),
            "live_topics": ["metrics", "health"],
        }

    def _alarms(self, values):
        """Return the metrics whose correct value is zero."""

        return [
            {
                "name": name,
                "value": values.get(name, 0.0),
                "ok": not values.get(name, 0.0),
                "meaning": meaning,
            }
            for name, meaning in sorted(ALARM_METRICS.items())
        ]

    def _caches(self, values):
        """Return hit rate per cache, beside its invalidation contract."""

        rows = []
        for cache in CACHE_CONTRACTS:
            hits = values.get(cache["hit"], 0.0)
            misses = values.get(cache["miss"], 0.0)
            total = hits + misses
            rows.append(
                {
                    "name": cache["name"],
                    "hits": hits,
                    "misses": misses,
                    "hit_rate": round(hits / total, 4) if total else None,
                    "invalidation": cache["invalidation"],
                    "used": bool(total),
                }
            )
        return rows

    def _systems(self):
        """Return every registered scheduler system with cadence and scope."""

        try:
            from evennia.utils import systems
        except Exception:  # noqa: BLE001
            return {"available": False, "rows": []}

        registry = (
            getattr(systems, "_SYSTEMS", None)
            or getattr(systems, "REGISTRY", None)
            or getattr(systems, "_REGISTRY", None)
        )
        if registry is None:
            return {
                "available": False,
                "rows": [],
                "reason": "The scheduler does not expose a readable registry.",
            }
        rows = []
        try:
            entries = registry.values() if hasattr(registry, "values") else list(registry)
            for entry in entries:
                rows.append(
                    {
                        "name": str(getattr(entry, "name", entry))[:120],
                        "cadence": str(getattr(entry, "cadence", ""))[:80],
                        "scope": str(getattr(entry, "scope", ""))[:80],
                        "last_run": str(getattr(entry, "last_run", "") or ""),
                    }
                )
        except Exception:  # noqa: BLE001
            return {"available": False, "rows": []}
        return {"available": True, "rows": sorted(rows, key=lambda row: row["name"])}

    def _tasks(self, values):
        """Return supervised task-root counts."""

        return {
            "active": values.get("evennia_runtime_tasks_active", 0.0),
            "started": values.get("evennia_runtime_tasks_total", 0.0),
            "db_scope_closes": values.get("evennia_runtime_db_scope_closes_total", 0.0),
        }
