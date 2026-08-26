"""
PostgreSQL-oriented Django database settings helpers for production deployments.

Use with PgBouncer or ``django-db-connection-pool`` in the game ``settings.py``.
The game thread should use the primary ``default`` database only; read replicas
are for website/logs/analytics — never for live command processing.

Session-level tunables (``statement_timeout``, ``default_transaction_read_only``)
are applied via a ``connection_created`` receiver issuing ``SET`` after the
handshake, **not** via the ``OPTIONS["options"] = "-c ..."`` startup parameter.
PgBouncer in transaction-pool mode rejects the ``options`` startup parameter at
the protocol level::

    FATAL: unsupported startup parameter in options: statement_timeout

Operators can whitelist via ``ignore_startup_parameters = options`` in
pgbouncer.ini, but many ops teams cannot edit that file and the fork's
default should work out-of-the-box behind the most common pooler config.

Caveat for pure transaction-pool deployments: session-level ``SET`` may not
persist across backend rebinding. For maximum guarantees under transaction
pooling, set ``statement_timeout`` at the role level
(``ALTER ROLE app_user SET statement_timeout = '30s'``) and treat this
signal-based setter as best-effort on top.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Set

from django.db.backends.signals import connection_created
from django.dispatch import receiver

# Aliases registered by ``build_read_replica_entry``. Read by the
# ``connection_created`` receiver to issue ``SET default_transaction_read_only = on``
# on those connections.
_READ_REPLICA_ALIASES: Set[str] = set()


def apply_postgres_engine_defaults(databases: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of ``DATABASES`` with Underspire engine connection defaults applied
    to PostgreSQL backends.

    Applies ``CONN_MAX_AGE`` / ``CONN_HEALTH_CHECKS`` here. When
    ``ENGINE_DATABASE_TRANSACTION_POOLING`` is enabled, persistent connections
    and server-side cursors are forcibly disabled because neither may outlive a
    transaction-pool backend assignment. The ``statement_timeout`` is *not* set
    in ``OPTIONS`` — see the ``connection_created`` receiver below.
    """
    from django.conf import settings

    out = deepcopy(databases)
    conn_max_age = int(getattr(settings, "ENGINE_DATABASE_CONN_MAX_AGE", 600) or 0)
    health_checks = bool(getattr(settings, "ENGINE_DATABASE_CONN_HEALTH_CHECKS", True))
    transaction_pooling = bool(
        getattr(settings, "ENGINE_DATABASE_TRANSACTION_POOLING", False)
    )

    for alias, cfg in out.items():
        if not isinstance(cfg, dict):
            continue
        engine = cfg.get("ENGINE", "")
        if "postgresql" not in engine and "postgres" not in engine:
            continue
        if transaction_pooling:
            cfg["CONN_MAX_AGE"] = 0
            cfg["DISABLE_SERVER_SIDE_CURSORS"] = True
        elif conn_max_age > 0:
            cfg.setdefault("CONN_MAX_AGE", conn_max_age)
        if health_checks:
            cfg.setdefault("CONN_HEALTH_CHECKS", True)
    return out


def build_read_replica_entry(
    primary: Dict[str, Any], *, name: str = "replica"
) -> Dict[str, Any]:
    """
    Clone primary config for a read replica alias (website, logs, analytics only).

    Side effect: registers ``name`` so the ``connection_created`` receiver issues
    ``SET default_transaction_read_only = on`` on that alias's connections. The
    caller is responsible for assigning the returned dict at
    ``DATABASES[name]`` — the ``name`` argument here only controls the
    read-only registration.
    """
    cfg = deepcopy(primary)
    _READ_REPLICA_ALIASES.add(name)
    return cfg


@receiver(connection_created, dispatch_uid="evennia.engine_pg_session_init")
def _apply_engine_pg_session_init(sender, connection, **kwargs):
    """Apply session-level PostgreSQL tunables after each connection handshake.

    Runs ``SET statement_timeout`` on the ``default`` alias and
    ``SET default_transaction_read_only = on`` on aliases registered via
    ``build_read_replica_entry``. Works through PgBouncer transaction-pool
    mode (unlike the rejected ``options=-c ...`` startup parameter); see
    module docstring for the transaction-pool caveat.
    """
    if connection.vendor != "postgresql":
        return
    from django.conf import settings

    stmts = []
    if connection.alias == "default":
        timeout_ms = int(
            getattr(settings, "ENGINE_DATABASE_STATEMENT_TIMEOUT_MS", 30000) or 0
        )
        if timeout_ms > 0:
            stmts.append("SET statement_timeout = %d" % timeout_ms)
    if connection.alias in _READ_REPLICA_ALIASES:
        stmts.append("SET default_transaction_read_only = on")
    if not stmts:
        return
    with connection.cursor() as cursor:
        for stmt in stmts:
            cursor.execute(stmt)
