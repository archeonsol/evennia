"""
The flag queue.

Everything automated in this package ends here. A flag is an observation with
evidence attached and a person's name on the other end of it; it changes nothing
about the account it names. Turning one into a sanction is a staff action, and
there is no code path that shortcuts that.

Flags deduplicate on a caller-supplied key. The same observation seen again bumps
``seen_count`` and ``last_seen_at`` on the existing row rather than filling the
queue with copies -- a shared address between two players will be observed on
every single login they make.
"""

from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone

from evennia.server.models import ModerationFlag, Sanction
from evennia.utils import logger


def make_dedupe_key(kind: str, *parts) -> str:
    """Stable key for one observation, independent of which side reported it."""
    joined = "|".join(sorted(str(part or "") for part in parts))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]
    return f"{kind}:{digest}"


def raise_flag(
    *,
    kind: str,
    dedupe_key: str,
    summary: str = "",
    severity: int = 0,
    account_id=None,
    account_name: str = "",
    session_uid: str = "",
    evidence=None,
) -> ModerationFlag | None:
    """
    Record an observation for staff review, or bump the existing one.

    A flag that staff already dismissed stays dismissed: re-observing the same
    thing reopens nothing, because "we looked at this and it was fine" is an
    answer that should not have to be given twice. It still counts the sighting,
    so a dismissed flag that keeps recurring is visible as such.
    """
    try:
        now = timezone.now()
        with transaction.atomic():
            flag, created = ModerationFlag.objects.get_or_create(
                dedupe_key=dedupe_key[:128],
                defaults={
                    "kind": kind,
                    "severity": int(severity),
                    "account_id": account_id,
                    "account_name": (account_name or "")[:255],
                    "session_uid": (session_uid or "")[:32],
                    "summary": (summary or "")[:512],
                    "evidence": evidence or {},
                    "created_at": now,
                    "last_seen_at": now,
                },
            )
            if not created:
                flag.seen_count += 1
                flag.last_seen_at = now
                # Keep the newest evidence; the old copy stays in the audit trail.
                if evidence:
                    flag.evidence = evidence
                if summary:
                    flag.summary = summary[:512]
                flag.save(update_fields=["seen_count", "last_seen_at", "evidence", "summary"])
        return flag
    except Exception:
        logger.log_trace("moderation.raise_flag failed (kind=%s)" % kind)
        return None


def resolve_flag(
    flag: ModerationFlag,
    *,
    state: str,
    actor=None,
    note: str = "",
    sanction: Sanction | None = None,
) -> ModerationFlag:
    """Record what a staff member decided about a flag."""
    if state not in dict(ModerationFlag.STATE_CHOICES):
        raise ValueError(f"Unknown flag state {state!r}.")

    flag.state = state
    flag.resolution_note = (note or "")[:512]
    flag.resolved_by_id = getattr(actor, "id", None)
    flag.resolved_by_name = str(getattr(actor, "username", "") or "")[:255]
    flag.resolved_at = timezone.now() if state not in ModerationFlag.OPEN_STATES else None
    if sanction is not None:
        flag.sanction = sanction
    flag.save(
        update_fields=[
            "state",
            "resolution_note",
            "resolved_by_id",
            "resolved_by_name",
            "resolved_at",
            "sanction",
        ]
    )
    return flag


def open_flags(limit: int = 100):
    """Flags awaiting a decision, most severe and most recent first."""
    return list(
        ModerationFlag.objects.filter(state__in=ModerationFlag.OPEN_STATES).order_by(
            "-severity", "-last_seen_at"
        )[:limit]
    )
