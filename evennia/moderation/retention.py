"""
Retention: forget the raw addresses, keep the ability to moderate.

Two sweeps, both nightly, both plain ORM on a non-typeclass model so they run in
a worker thread without touching game state.

**Address purge.** After the retention window, ``ip`` and ``peer_ip`` are
cleared. ``ip_hash``, ``cidr``, ``asn`` and the client columns stay, so a
year-old session still answers "was this the same network" and "was this the
same client" without the address itself still sitting in the table. A stored
address stops being evidence long before it stops being a liability.

**Row purge.** One row per connection means the table only grows. Past the row
retention window the rows go entirely. Deletion runs in bounded batches: a
single statement over a year of sessions would hold one long transaction and
bloat the table it is trying to shrink.

Both windows are settings, and both accept 0 for "never". Neither sweep touches
``Sanction``, ``SanctionHit`` or ``ModerationFlag``: those carry their own
evidence and are the record of a decision a person made.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from evennia.utils import logger

# Rows deleted per statement, and statements per run. The product bounds one
# night's work; anything past it waits for tomorrow, which is fine for a sweep
# whose deadline is measured in days.
_DELETE_BATCH = 5000
_MAX_BATCHES = 40


def _window(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default) or 0)
    except (TypeError, ValueError):
        return 0


def purge_addresses(days=None) -> int:
    """
    Clear raw addresses on sessions older than the retention window.

    Returns the number of rows changed. 0 days disables the sweep.
    """
    from evennia.server.models import SessionRecord

    days = _window("MODERATION_IP_RETENTION_DAYS", 90) if days is None else int(days)
    if days <= 0:
        return 0

    cutoff = timezone.now() - timedelta(days=days)
    return (
        SessionRecord.objects.filter(connected_at__lt=cutoff)
        .exclude(ip__isnull=True, peer_ip__isnull=True)
        .update(ip=None, peer_ip=None)
    )


def purge_sessions(days=None) -> int:
    """
    Delete session rows older than the row-retention window, in batches.

    Returns the number of rows deleted. 0 days disables the sweep.
    """
    from evennia.server.models import SessionRecord

    days = _window("MODERATION_SESSION_RETENTION_DAYS", 365) if days is None else int(days)
    if days <= 0:
        return 0

    cutoff = timezone.now() - timedelta(days=days)
    deleted = 0
    for _batch in range(_MAX_BATCHES):
        ids = list(
            SessionRecord.objects.filter(connected_at__lt=cutoff).values_list("id", flat=True)[
                :_DELETE_BATCH
            ]
        )
        if not ids:
            break
        count, _detail = SessionRecord.objects.filter(id__in=ids).delete()
        deleted += count
        if len(ids) < _DELETE_BATCH:
            break
    return deleted


def run_retention() -> dict:
    """Both sweeps. Blocking; call from a worker thread. Never raises."""
    result = {"addresses_cleared": 0, "sessions_deleted": 0}
    # Rows first: clearing the address on a row that is about to be deleted is a
    # write nobody ever reads.
    try:
        result["sessions_deleted"] = purge_sessions()
    except Exception:
        logger.log_trace("moderation.purge_sessions failed")
    try:
        result["addresses_cleared"] = purge_addresses()
    except Exception:
        logger.log_trace("moderation.purge_addresses failed")
    return result
