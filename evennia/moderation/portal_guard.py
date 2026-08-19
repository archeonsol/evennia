"""
Portal-side connection guard.

Refuses a connection before it reaches the Server process when the address is
covered by an active blocking sanction. This is the cheapest possible drop, and
it is the one that still works while the game is under a connection flood.

The refusal text names no mechanism. An evader who is told which signal matched
knows exactly what to change; one who is told only that they cannot connect, and
where to appeal, does not.

Address subjects only. Account names are unknown at connect time, and device
tokens and client fingerprints arrive after negotiation -- those are matched
Server-side at authentication instead.
"""

from __future__ import annotations

from django.conf import settings

from evennia.utils import logger

REFUSAL_TEXT = (
    "This connection has been refused. If you believe this is a mistake, contact staff.\r\n"
)
REFUSAL_BYTES = REFUSAL_TEXT.encode("utf-8")


def refuses(address) -> bool:
    """Whether the Portal should drop this connection immediately.

    Args:
        address (str): Client address, or None when it could not be read.

    Returns:
        bool: True when an active blocking sanction covers the address. Any
            failure returns False, leaving the decision to the Server-side check
            against the live table.
    """
    try:
        if not getattr(settings, "MODERATION_PORTAL_BLOCK_ENABLED", True):
            return False
        from evennia.moderation.blocklist import is_blocked_address

        return is_blocked_address(address)
    except Exception:
        logger.log_trace("moderation.portal_guard check failed")
        return False
