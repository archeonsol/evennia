"""
Address blocklist for the Portal.

The Portal accepts connections on the reactor thread and has no business making
a database query there. So the Server publishes a compact snapshot of the
address subjects of every active blocking sanction, and the Portal reads it from
the shared cache behind a short in-process TTL: one cache read every
``BLOCKLIST_TTL`` seconds, and a set lookup per connection.

Only address-shaped subjects are published. Account names are not known at
connect time, and device tokens and client fingerprints arrive after
negotiation, so those stay with the Server-side check at authentication.

The blocklist is a performance cache, never the source of truth. A stale or
missing snapshot means a sanctioned address gets as far as the login prompt,
where :func:`evennia.moderation.enforcement.check_login` refuses it against the
live table.
"""

from __future__ import annotations

import time

from django.core.cache import cache

from evennia.utils import logger

CACHE_KEY = "evennia:moderation:blocklist"
CACHE_TTL = 3600
# How long the Portal reuses its in-process copy before re-reading the cache.
BLOCKLIST_TTL = 30

_local = {"fetched_at": 0.0, "value": None}


def build_snapshot() -> dict:
    """Address subjects of every active blocking sanction.

    Each entry carries its own expiry so a sanction that runs out stops being
    enforced at the exact moment it should, rather than whenever the Portal next
    happens to refresh.

    Returns:
        dict: ``{"ips": {value: expiry_ts}, "cidrs": {...}, "version": float}``,
            where an expiry of 0 means indefinite.
    """
    from evennia.server.models import Sanction

    rows = Sanction.objects.active().filter(level__in=Sanction.BLOCKING_LEVELS)
    ips = {}
    cidrs = {}
    for subject_type, subject_value, expires_at in rows.values_list(
        "subject_type", "subject_value", "expires_at"
    ):
        bucket = None
        if subject_type == Sanction.SUBJECT_IP:
            bucket = ips
        elif subject_type == Sanction.SUBJECT_CIDR:
            bucket = cidrs
        if bucket is None:
            continue
        expiry = expires_at.timestamp() if expires_at else 0.0
        # Longest-lived wins when two sanctions cover the same subject.
        current = bucket.get(subject_value)
        if current is None or expiry == 0.0 or (current and expiry > current):
            bucket[subject_value] = expiry

    return {"ips": ips, "cidrs": cidrs, "version": time.time()}


def rebuild() -> dict:
    """Recompute the snapshot and publish it. Call after any sanction change.

    Returns:
        dict: The snapshot that was published, or an empty one on failure.
    """
    try:
        snapshot = build_snapshot()
        cache.set(CACHE_KEY, snapshot, CACHE_TTL)
        _local["value"] = snapshot
        _local["fetched_at"] = time.time()
        return snapshot
    except Exception:
        logger.log_trace("moderation.blocklist rebuild failed")
        return {"ips": {}, "cidrs": {}, "version": 0}


def get_snapshot() -> dict:
    """Current snapshot, re-read from the cache at most every ``BLOCKLIST_TTL``.

    Returns:
        dict: The snapshot; empty when the cache is unreachable, which fails
            open by design -- see the module docstring.
    """
    now = time.time()
    if _local["value"] is not None and now - _local["fetched_at"] < BLOCKLIST_TTL:
        return _local["value"]
    try:
        snapshot = cache.get(CACHE_KEY)
    except Exception:
        logger.log_trace("moderation.blocklist cache read failed")
        snapshot = None
    if not snapshot:
        snapshot = {"ips": {}, "cidrs": {}, "version": 0}
    _local["value"] = snapshot
    _local["fetched_at"] = now
    return snapshot


def is_blocked_address(address) -> bool:
    """Whether a connecting address is covered by a blocking sanction.

    Args:
        address (str): Client address, or None.

    Returns:
        bool: True when the address or its network is sanctioned.
    """
    from evennia.moderation.capture import derive_cidr, normalize_address

    normalized = normalize_address(address)
    if not normalized:
        return False

    snapshot = get_snapshot()
    now = time.time()

    def _live(bucket, key):
        if not key:
            return False
        expiry = bucket.get(key)
        if expiry is None:
            return False
        return expiry == 0.0 or expiry > now

    if _live(snapshot.get("ips") or {}, normalized):
        return True
    return _live(snapshot.get("cidrs") or {}, derive_cidr(normalized))


def reset_local_cache() -> None:
    """Drop the in-process copy. For tests and for forced refreshes."""
    _local["value"] = None
    _local["fetched_at"] = 0.0
