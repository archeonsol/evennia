"""
PostgreSQL-oriented Django database settings helpers for production deployments.

Use with PgBouncer or ``django-db-connection-pool`` in the game ``settings.py``.
The game thread should use the primary ``default`` database only; read replicas
are for website/logs/analytics — never for live command processing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


def apply_postgres_engine_defaults(databases: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of ``DATABASES`` with Underspire engine connection defaults applied
    to PostgreSQL backends.
    """
    from django.conf import settings

    out = deepcopy(databases)
    conn_max_age = int(getattr(settings, "ENGINE_DATABASE_CONN_MAX_AGE", 600) or 0)
    health_checks = bool(getattr(settings, "ENGINE_DATABASE_CONN_HEALTH_CHECKS", True))
    statement_timeout_ms = int(
        getattr(settings, "ENGINE_DATABASE_STATEMENT_TIMEOUT_MS", 30000) or 0
    )

    for alias, cfg in out.items():
        if not isinstance(cfg, dict):
            continue
        engine = cfg.get("ENGINE", "")
        if "postgresql" not in engine and "postgres" not in engine:
            continue
        if conn_max_age > 0:
            cfg.setdefault("CONN_MAX_AGE", conn_max_age)
        if health_checks:
            cfg.setdefault("CONN_HEALTH_CHECKS", True)
        opts = dict(cfg.get("OPTIONS") or {})
        if statement_timeout_ms > 0 and alias == "default":
            opts.setdefault("options", "-c statement_timeout=%s" % statement_timeout_ms)
        if opts:
            cfg["OPTIONS"] = opts
    return out


def build_read_replica_entry(primary: Dict[str, Any], *, name: str = "replica") -> Dict[str, Any]:
    """
    Clone primary config for a read replica alias (website, logs, analytics only).
    """
    cfg = deepcopy(primary)
    cfg["OPTIONS"] = dict(cfg.get("OPTIONS") or {})
    cfg["OPTIONS"]["options"] = (cfg["OPTIONS"].get("options") or "") + " -c default_transaction_read_only=on"
    return cfg
