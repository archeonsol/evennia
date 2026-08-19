"""
Sanction issue, revoke, and match.

A sanction is only ever created here, and only ever on behalf of a named staff
account. There is no code path in this package that issues one from a detection
result: automated signals produce :class:`~evennia.server.models.ModerationFlag`
rows instead, and a person decides what happens next.

Matching is exact. A sanction on ``cidr=203.0.113.0/24`` matches a session whose
derived ``cidr`` column is that exact string; nothing is scored, weighted, or
inferred. That is what makes an enforcement explainable to the player it lands
on -- and what makes a wrong one obvious in review.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from evennia.moderation.capture import derive_cidr, normalize_address
from evennia.server.models import Sanction, SanctionHit
from evennia.utils import logger


class SanctionError(Exception):
    """Raised for a malformed sanction request. The caller reports it to staff."""


@dataclass(frozen=True)
class Decision:
    """Outcome of matching a set of connection keys against active sanctions."""

    level: str = ""
    sanctions: tuple = field(default_factory=tuple)
    matched_on: tuple = field(default_factory=tuple)

    @property
    def blocks(self) -> bool:
        return self.level in Sanction.BLOCKING_LEVELS

    @property
    def reason(self) -> str:
        """Player-facing reason of the most severe match, if it has one."""
        for sanction in self.sanctions:
            if sanction.level == self.level and sanction.reason:
                return sanction.reason
        return ""


def normalize_subject(subject_type: str, value) -> str:
    """
    Canonical stored form of a sanction subject.

    Normalizing on write means matching is a plain string comparison on an
    indexed column rather than a regex sweep over every ban ever issued -- which
    is what the tuple list it replaces did, and why it could not see IPv6.
    """
    value = str(value or "").strip()
    if not value:
        raise SanctionError("A sanction needs a subject.")

    if subject_type == Sanction.SUBJECT_ACCOUNT:
        return value.lower()[:255]

    if subject_type == Sanction.SUBJECT_IP:
        address = normalize_address(value)
        if not address:
            raise SanctionError(f"{value!r} is not an address.")
        return address

    if subject_type == Sanction.SUBJECT_CIDR:
        if "/" not in value:
            # A bare address means the network it sits in. ip_network would
            # happily accept it as a /32 or /128, which would then match nothing
            # the capture layer ever writes, since ``cidr`` there is already
            # widened to /24 or /64.
            derived = derive_cidr(normalize_address(value))
            if not derived:
                raise SanctionError(f"{value!r} is not a network.")
            return derived
        try:
            return str(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise SanctionError(f"{value!r} is not a network.")

    if subject_type == Sanction.SUBJECT_ASN:
        digits = value.upper().removeprefix("AS").strip()
        if not digits.isdigit():
            raise SanctionError(f"{value!r} is not an AS number.")
        return digits

    if subject_type == Sanction.SUBJECT_EMAIL_DOMAIN:
        return value.lower().lstrip("@")[:255]

    # Opaque hex identifiers: device_token, client_fp, csessid.
    return value.lower()[:255]


def _canonical(sanction: Sanction) -> str:
    return json.dumps(
        {
            "subject_type": sanction.subject_type,
            "subject_value": sanction.subject_value,
            "level": sanction.level,
            "reason": sanction.reason,
            "actor_name": sanction.actor_name,
            "created_at": sanction.created_at.isoformat(),
            "expires_at": sanction.expires_at.isoformat() if sanction.expires_at else "",
            "silent": bool(sanction.silent),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _chain_hash(prev_hash: str, sanction: Sanction) -> str:
    return hashlib.sha256((prev_hash + _canonical(sanction)).encode("utf-8")).hexdigest()


def _last_chain_hash():
    """Tail of the hash chain, locked for update where the backend supports it."""
    rows = Sanction.objects.order_by("-id")
    try:
        row = rows.select_for_update().first()
    except Exception:
        row = rows.first()
    return row.row_hash if row else ""


def issue_sanction(
    *,
    subject_type: str,
    subject_value,
    level: str = Sanction.LEVEL_BAN,
    actor=None,
    reason: str = "",
    staff_note: str = "",
    expires_at=None,
    evidence=None,
    silent: bool = False,
) -> Sanction:
    """
    Create a sanction on behalf of ``actor``.

    ``actor`` is the staff account making the decision. It is optional only so
    the legacy-banlist import can record that no living person chose these; every
    other caller must pass one.
    """
    if subject_type not in dict(Sanction.SUBJECT_CHOICES):
        raise SanctionError(f"Unknown subject type {subject_type!r}.")
    if level not in dict(Sanction.LEVEL_CHOICES):
        raise SanctionError(f"Unknown level {level!r}.")

    value = normalize_subject(subject_type, subject_value)

    with transaction.atomic():
        prev_hash = _last_chain_hash()
        sanction = Sanction(
            subject_type=subject_type,
            subject_value=value,
            level=level,
            reason=(reason or "").strip(),
            staff_note=(staff_note or "").strip(),
            actor_id=getattr(actor, "id", None),
            actor_name=str(getattr(actor, "username", "") or "")[:255],
            created_at=timezone.now(),
            expires_at=expires_at,
            evidence=evidence or {},
            silent=bool(silent),
            prev_hash=prev_hash,
        )
        sanction.row_hash = _chain_hash(prev_hash, sanction)
        sanction.save()

    _refresh_blocklist()
    _audit("sanction_issue", sanction, actor)
    return sanction


def revoke_sanction(sanction: Sanction, *, actor=None, reason: str = "") -> Sanction:
    """Lift a sanction. The row stays; history is never deleted."""
    if sanction.revoked_at is not None:
        return sanction
    sanction.revoked_at = timezone.now()
    sanction.revoked_by_id = getattr(actor, "id", None)
    sanction.revoked_by_name = str(getattr(actor, "username", "") or "")[:255]
    sanction.revoked_reason = (reason or "").strip()[:255]
    sanction.save(
        update_fields=["revoked_at", "revoked_by_id", "revoked_by_name", "revoked_reason"]
    )
    _refresh_blocklist()
    _audit("sanction_revoke", sanction, actor)
    return sanction


def _refresh_blocklist() -> None:
    """Republish the Portal's address snapshot. Best-effort."""
    try:
        from evennia.moderation.blocklist import rebuild

        rebuild()
    except Exception:
        logger.log_trace("moderation.sanctions blocklist refresh failed")


def _audit(action: str, sanction: Sanction, actor) -> None:
    """Mirror the decision onto the engine event bus. Best-effort."""
    try:
        from evennia.eventbus import emit

        emit(
            f"moderation.{action}",
            {
                "sanction_id": sanction.id,
                "subject_type": sanction.subject_type,
                "subject_value": sanction.subject_value,
                "level": sanction.level,
                "silent": bool(sanction.silent),
                "reason": (sanction.reason or sanction.revoked_reason or "")[:200],
                "actor_name": str(getattr(actor, "username", "") or ""),
            },
            actor=actor,
        )
    except Exception:
        logger.log_trace("moderation.sanctions event emit failed")


def keys_from_snapshot(snapshot: dict) -> dict:
    """
    Connection keys a sanction can be matched against.

    Empty values are dropped so an unpopulated column can never match a sanction
    whose subject happens to be the empty string.
    """
    keys = {
        Sanction.SUBJECT_ACCOUNT: (snapshot.get("account_name") or "").lower(),
        Sanction.SUBJECT_IP: snapshot.get("ip") or "",
        Sanction.SUBJECT_CIDR: snapshot.get("cidr") or "",
        Sanction.SUBJECT_DEVICE: snapshot.get("device_token") or "",
        Sanction.SUBJECT_CLIENT_FP: snapshot.get("client_fp") or "",
        Sanction.SUBJECT_CSESSID: snapshot.get("csessid") or "",
    }
    asn = snapshot.get("asn")
    if asn:
        keys[Sanction.SUBJECT_ASN] = str(asn)
    return {key: value for key, value in keys.items() if value}


def match_sanctions(keys: dict, *, now=None):
    """Active sanctions matching any of ``keys``, most severe first."""
    if not keys:
        return []

    from django.db.models import Q

    query = Q()
    for subject_type, value in keys.items():
        query |= Q(subject_type=subject_type, subject_value=value)

    matches = list(Sanction.objects.active(now=now).filter(query))
    matches.sort(key=lambda sanction: (sanction.severity(), sanction.created_at), reverse=True)
    return matches


def evaluate(keys: dict, *, now=None) -> Decision:
    """Most severe active sanction covering these keys, with what matched."""
    matches = match_sanctions(keys, now=now)
    if not matches:
        return Decision()
    top = matches[0].level
    matched_on = tuple(sanction.subject_type for sanction in matches if sanction.level == top)
    return Decision(level=top, sanctions=tuple(matches), matched_on=matched_on)


def record_hits(decision: Decision, snapshot: dict, *, action: str) -> None:
    """Log that these sanctions fired. Best-effort; never breaks enforcement."""
    if not decision.sanctions:
        return
    try:
        SanctionHit.objects.bulk_create(
            [
                SanctionHit(
                    sanction=sanction,
                    action_taken=action,
                    matched_on=sanction.subject_type,
                    session_uid=(snapshot.get("session_uid") or "")[:32],
                    account_name=(snapshot.get("account_name") or "")[:255],
                    ip=snapshot.get("ip") or None,
                    cidr=(snapshot.get("cidr") or "")[:64],
                    device_token=(snapshot.get("device_token") or "")[:64],
                    client_fp=(snapshot.get("client_fp") or "")[:64],
                )
                for sanction in decision.sanctions
            ]
        )
    except Exception:
        logger.log_trace("moderation.record_hits failed")


def verify_chain(limit: int = 0) -> dict:
    """
    Walk the hash chain and report the first row that does not verify.

    A break means a row was edited or deleted outside :func:`issue_sanction`. It
    does not say who did it, only that the log is no longer what it was.
    """
    rows = Sanction.objects.order_by("id")
    if limit:
        rows = rows[:limit]

    prev_hash = ""
    checked = 0
    for sanction in rows:
        expected = _chain_hash(prev_hash, sanction)
        if sanction.prev_hash != prev_hash or sanction.row_hash != expected:
            return {"ok": False, "checked": checked, "broken_at": sanction.id}
        prev_hash = sanction.row_hash
        checked += 1
    return {"ok": True, "checked": checked, "broken_at": None}
