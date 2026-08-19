"""
One-time import of the legacy ``server_bans`` list into sanctions.

The list it replaces is a pickled tuple of
``(name, ip, compiled_regex, ctime_string, reason)`` in ``ServerConfig``. It has
no expiry, no evidence, no issuing staff member, and no revocation history; its
address matching is a regex built by substituting ``[0-9]{1,3}`` for ``*``, which
cannot express IPv6 at all.

Import is idempotent and non-destructive. The original list is archived under a
separate ``ServerConfig`` key before ``server_bans`` is cleared, so the pre-import
state is always recoverable.
"""

from __future__ import annotations

import ipaddress

from django.utils import timezone

from evennia.server.models import Sanction, ServerConfig
from evennia.utils import logger

ARCHIVE_KEY = "server_bans_archived"
MARKER_KEY = "moderation_banlist_imported_at"


def _wildcard_to_network(value: str) -> str:
    """
    Widen a legacy ``a.b.c.*`` pattern into the network it was standing in for.

    Returns an empty string when the pattern is not a clean octet-boundary
    wildcard, in which case the caller keeps it as a single address instead of
    guessing at a range.
    """
    parts = value.split(".")
    if len(parts) != 4:
        return ""
    wildcards = [index for index, part in enumerate(parts) if part == "*"]
    if not wildcards:
        return ""
    # Wildcards must be a contiguous run ending at the last octet.
    if wildcards != list(range(wildcards[0], 4)):
        return ""
    prefix = wildcards[0] * 8
    concrete = [part if part != "*" else "0" for part in parts]
    try:
        network = ipaddress.ip_network(f"{'.'.join(concrete)}/{prefix}", strict=False)
    except ValueError:
        return ""
    return str(network)


def _sanction_args(entry):
    """Map one legacy tuple onto sanction subject arguments, or None to skip."""
    try:
        name = (entry[0] or "").strip()
        address = (entry[1] or "").strip()
        stamp = (entry[3] or "").strip() if len(entry) > 3 else ""
        reason = (entry[4] or "").strip() if len(entry) > 4 else ""
    except (IndexError, TypeError):
        return None

    if name:
        return Sanction.SUBJECT_ACCOUNT, name.lower(), stamp, reason
    if not address:
        return None

    if "*" in address:
        network = _wildcard_to_network(address)
        if network:
            return Sanction.SUBJECT_CIDR, network, stamp, reason
        return None
    return Sanction.SUBJECT_IP, address, stamp, reason


def import_server_bans(*, clear: bool = True) -> dict:
    """
    Convert every legacy ban into a sanction.

    Imported sanctions carry no ``actor``: nobody living chose them in this
    system, and recording a staff member who did not issue them would be a lie
    in an audit log. They are indefinite, matching their original semantics --
    the legacy format could not express an expiry, so inventing one here would
    silently unban people.

    Returns a summary dict. Safe to call on every start; it no-ops once the
    marker is set.
    """
    summary = {"imported": 0, "skipped": 0, "already_done": False}

    if ServerConfig.objects.conf(MARKER_KEY):
        summary["already_done"] = True
        return summary

    banlist = ServerConfig.objects.conf("server_bans") or []
    if not banlist:
        ServerConfig.objects.conf(MARKER_KEY, timezone.now().isoformat())
        return summary

    from evennia.moderation.sanctions import SanctionError, issue_sanction

    # Archive before touching anything, so the pre-import state is recoverable
    # even if the conversion below turns out to have been wrong.
    ServerConfig.objects.conf(ARCHIVE_KEY, list(banlist))

    for entry in banlist:
        parsed = _sanction_args(entry)
        if parsed is None:
            summary["skipped"] += 1
            logger.log_warn("moderation: could not import legacy ban %r" % (entry,))
            continue
        subject_type, subject_value, stamp, reason = parsed
        try:
            issue_sanction(
                subject_type=subject_type,
                subject_value=subject_value,
                level=Sanction.LEVEL_BAN,
                actor=None,
                reason=reason,
                staff_note="Imported from the legacy server_bans list.",
                evidence={
                    "source": "legacy_server_bans",
                    "original": [str(part) for part in entry[:2]],
                    "original_date": stamp,
                },
            )
        except SanctionError as error:
            summary["skipped"] += 1
            logger.log_warn("moderation: legacy ban %r rejected: %s" % (entry, error))
            continue
        summary["imported"] += 1

    if clear:
        ServerConfig.objects.conf("server_bans", [])
    ServerConfig.objects.conf(MARKER_KEY, timezone.now().isoformat())

    logger.log_info(
        "moderation: imported %s legacy bans, skipped %s"
        % (summary["imported"], summary["skipped"])
    )
    return summary
