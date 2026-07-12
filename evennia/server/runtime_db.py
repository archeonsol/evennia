"""Database-lifecycle enforcement for the asyncio game runtime."""

from __future__ import annotations

import asyncio

from django.conf import settings
from django.db.backends.signals import connection_created
from django.dispatch import receiver

from evennia.utils import clock, logger


@receiver(connection_created, dispatch_uid="evennia.runtime_db_scope_audit")
def audit_runtime_connection(sender, connection, **kwargs):
    """Flag ORM connections opened by an unmanaged asyncio context.

    Synchronous management commands and web requests are outside this engine
    invariant. On the game loop, however, every ORM-capable detached root must
    be owned by :func:`evennia.utils.clock.run_coroutine` so it receives a
    deterministic closing boundary.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    if clock.current_runtime_task_kind() is not None:
        return

    try:
        from evennia.server.prometheus_metrics import record_unmanaged_db_connection

        record_unmanaged_db_connection()
    except Exception:
        pass

    message = (
        "Django connection opened in an unmanaged asyncio task; use "
        "clock.run_coroutine(..., task_kind=...) for detached runtime roots."
    )
    policy = str(getattr(settings, "ENGINE_RUNTIME_UNMANAGED_DB_POLICY", "warn")).lower()
    if policy == "error":
        raise RuntimeError(message)
    logger.log_warn(message)
