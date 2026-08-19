"""
Connection-time enforcement.

The only thing enforced is a sanction a person already issued. Nothing here
decides to ban anybody: it looks up whether an active sanction covers the keys
this connection presents, and reports what it found.

Two entry points, deliberately different in what they can see:

``evaluate_connection`` takes every key the caller has and is used by the portal,
which knows the address and the negotiated client before a login is attempted.

``check_login`` takes only a username and an address, because that is all
``DefaultAccount.is_banned`` is handed at authentication time.
"""

from __future__ import annotations

from django.conf import settings

from evennia.moderation.capture import derive_cidr, normalize_address
from evennia.moderation.sanctions import Decision, evaluate, record_hits
from evennia.server.models import Sanction, SanctionHit
from evennia.utils import logger

# Shown when a sanction refuses a connection. Deliberately says nothing about
# which signal matched: naming it teaches an evader exactly what to change, and
# the appeal route is the only thing the player can usefully act on.
DEFAULT_BLOCK_MESSAGE = (
    "This account cannot connect. If you believe this is a mistake, contact staff."
)


def connection_keys(
    *,
    username: str = "",
    ip=None,
    device_token: str = "",
    client_fp: str = "",
    csessid: str = "",
    asn=None,
) -> dict:
    """Normalized subject keys for whatever the caller was able to observe."""
    address = normalize_address(ip)
    keys = {
        Sanction.SUBJECT_ACCOUNT: str(username or "").lower().strip(),
        Sanction.SUBJECT_IP: address or "",
        Sanction.SUBJECT_CIDR: derive_cidr(address),
        Sanction.SUBJECT_DEVICE: str(device_token or "").lower(),
        Sanction.SUBJECT_CLIENT_FP: str(client_fp or "").lower(),
        Sanction.SUBJECT_CSESSID: str(csessid or "").lower(),
    }
    if asn:
        keys[Sanction.SUBJECT_ASN] = str(asn)
    return {key: value for key, value in keys.items() if value}


def evaluate_connection(**observed) -> Decision:
    """
    Most severe active sanction covering this connection.

    Never raises. What a failed lookup means is a policy choice, set by
    ``MODERATION_FAIL_CLOSED``: by default the connection is let through, on the
    grounds that a database fault locking out the whole playerbase is worse than
    a sanctioned player getting one more session. Setting it True takes the
    fail-closed reading instead and refuses everyone while the lookup is broken.
    """
    try:
        return evaluate(connection_keys(**observed))
    except Exception:
        logger.log_trace("moderation.evaluate_connection failed")
        if getattr(settings, "MODERATION_FAIL_CLOSED", False):
            return Decision(level=Sanction.LEVEL_BAN, matched_on=("lookup_failed",))
        return Decision()


def check_login(username: str = "", ip=None) -> Decision:
    """Sanction decision for an authentication attempt."""
    return evaluate_connection(username=username, ip=ip)


def note_enforcement(decision: Decision, *, action: str = SanctionHit.ACTION_BLOCKED, **observed):
    """Record that a decision fired. Best-effort; never raises."""
    if not decision.sanctions:
        return
    address = normalize_address(observed.get("ip"))
    snapshot = {
        "session_uid": observed.get("session_uid", ""),
        "account_name": observed.get("username", ""),
        "ip": address,
        "cidr": derive_cidr(address),
        "device_token": observed.get("device_token", ""),
        "client_fp": observed.get("client_fp", ""),
    }
    try:
        record_hits(decision, snapshot, action=action)
    except Exception:
        logger.log_trace("moderation.note_enforcement failed")


def block_message(decision: Decision) -> str:
    """
    Player-facing refusal text.

    A staff-written reason is shown when there is one; the mechanism that
    matched never is.
    """
    reason = decision.reason
    if reason:
        return f"{DEFAULT_BLOCK_MESSAGE}\n{reason}"
    return DEFAULT_BLOCK_MESSAGE
