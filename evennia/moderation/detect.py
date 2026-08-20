"""
Detectors over recorded sessions.

Each detector answers one exact-match question about the session that just
ended, and writes a flag when the answer is interesting. None of them sanctions
anybody, and none of them scores: a flag says "these two accounts used the same
device token", not "these are 87% likely to be the same person".

Bare address sharing is deliberately not a detector. Carrier-grade NAT,
universities, and households put unrelated players on one network constantly, so
flagging it would bury the queue in noise and train staff to ignore it. Address
history is still recorded and still queryable from the account page; it just does
not raise a flag on its own.

Runs in the worker thread that wrote the session row, after the write.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from evennia.moderation.flags import make_dedupe_key, raise_flag
from evennia.server.models import ModerationFlag, Sanction, SessionRecord
from evennia.utils import logger

# Severity is a sort order for the review queue, not a probability.
SEVERITY = {
    ModerationFlag.KIND_SANCTIONED_KEY: 4,
    ModerationFlag.KIND_IDENTITY_CORRELATION: 3,
    ModerationFlag.KIND_SHARED_DEVICE: 3,
    ModerationFlag.KIND_SHARED_CSESSID: 3,
    ModerationFlag.KIND_SHARED_CLIENT_CIDR: 1,
    ModerationFlag.KIND_TOR: 2,
    ModerationFlag.KIND_DATACENTER: 1,
    ModerationFlag.KIND_SIGNUP_BURST: 2,
}


def _lookback():
    days = int(getattr(settings, "MODERATION_DETECTION_LOOKBACK_DAYS", 90) or 0)
    if days <= 0:
        return None
    return timezone.now() - timedelta(days=days)


def _window(queryset):
    since = _lookback()
    return queryset.filter(connected_at__gte=since) if since else queryset


def _other_accounts(*, field: str, value: str, exclude_name: str) -> list:
    """Distinct account names that used the same key value, excluding this one."""
    if not value:
        return []
    rows = _window(SessionRecord.objects.filter(**{field: value}))
    names = set(rows.exclude(account_name="").values_list("account_name", flat=True).distinct())
    names.discard(exclude_name)
    return sorted(names)


def _flag_shared_key(snapshot: dict, *, kind: str, field: str, value: str, label: str):
    account_name = (snapshot.get("account_name") or "").strip()
    if not account_name or not value:
        return None
    others = _other_accounts(field=field, value=value, exclude_name=account_name)
    if not others:
        return None

    everyone = sorted({account_name, *others})
    return raise_flag(
        kind=kind,
        dedupe_key=make_dedupe_key(kind, value),
        severity=SEVERITY.get(kind, 0),
        account_id=snapshot.get("account_id"),
        account_name=account_name,
        session_uid=snapshot.get("session_uid", ""),
        summary=f"{len(everyone)} accounts share a {label}: {', '.join(everyone)}",
        evidence={
            "signal": kind,
            "key_field": field,
            "accounts": everyone,
            "observed_from_session": snapshot.get("session_uid", ""),
        },
    )


def detect_shared_device(snapshot: dict):
    """Another account has connected from this browser's device token."""
    return _flag_shared_key(
        snapshot,
        kind=ModerationFlag.KIND_SHARED_DEVICE,
        field="device_token",
        value=snapshot.get("device_token") or "",
        label="device token",
    )


def detect_shared_csessid(snapshot: dict):
    """Another account has connected from this browser session."""
    return _flag_shared_key(
        snapshot,
        kind=ModerationFlag.KIND_SHARED_CSESSID,
        field="csessid",
        value=snapshot.get("csessid") or "",
        label="browser session",
    )


def detect_shared_client_on_network(snapshot: dict):
    """
    Another account uses the same client build from the same network.

    Weak on its own -- a default client on a shared network is a common value --
    which is why it carries the lowest severity in the queue rather than being
    left out. It is corroboration for a stronger flag, not a finding.
    """
    account_name = (snapshot.get("account_name") or "").strip()
    client_fp = snapshot.get("client_fp") or ""
    cidr = snapshot.get("cidr") or ""
    if not (account_name and client_fp and cidr):
        return None

    rows = _window(SessionRecord.objects.filter(client_fp=client_fp, cidr=cidr))
    names = set(rows.exclude(account_name="").values_list("account_name", flat=True).distinct())
    names.discard(account_name)
    if not names:
        return None

    everyone = sorted({account_name, *names})
    kind = ModerationFlag.KIND_SHARED_CLIENT_CIDR
    return raise_flag(
        kind=kind,
        dedupe_key=make_dedupe_key(kind, client_fp, cidr),
        severity=SEVERITY.get(kind, 0),
        account_id=snapshot.get("account_id"),
        account_name=account_name,
        session_uid=snapshot.get("session_uid", ""),
        summary=(
            f"{len(everyone)} accounts share one client build on {cidr}: {', '.join(everyone)}"
        ),
        evidence={
            "signal": kind,
            "client_fp": client_fp,
            "cidr": cidr,
            "accounts": everyone,
        },
    )


def detect_sanctioned_key_reuse(snapshot: dict):
    """
    A key on this session was previously used by a currently-sanctioned account.

    This is the ban-evasion case, and it is deliberately detected after the fact
    rather than at the door. Refusing the connection outright would tell the
    evader exactly which signal to change next time; letting the session run and
    flagging it gives staff a decision to make with the evidence in front of
    them.
    """
    account_name = (snapshot.get("account_name") or "").strip()
    if not account_name:
        return None

    candidates = {}
    for field, label in (
        ("device_token", "device token"),
        ("csessid", "browser session"),
        ("ip_hash", "address"),
        # The client's own telnet stack, which a player cannot change without
        # changing client. client_fp is deliberately absent: it includes the
        # client name and terminal type, which are a settings dialog away.
        ("telnet_sig", "client negotiation"),
    ):
        value = snapshot.get(field) or ""
        for other in _other_accounts(field=field, value=value, exclude_name=account_name):
            candidates.setdefault(other, label)

    if not candidates:
        return None

    sanctioned = set(
        Sanction.objects.active()
        .filter(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value__in=[name.lower() for name in candidates],
            level__in=Sanction.BLOCKING_LEVELS,
        )
        .values_list("subject_value", flat=True)
    )
    hits = {name: label for name, label in candidates.items() if name.lower() in sanctioned}
    if not hits:
        return None

    kind = ModerationFlag.KIND_SANCTIONED_KEY
    listed = ", ".join(f"{name} (via {label})" for name, label in sorted(hits.items()))
    return raise_flag(
        kind=kind,
        dedupe_key=make_dedupe_key(kind, account_name, *sorted(hits)),
        severity=SEVERITY.get(kind, 0),
        account_id=snapshot.get("account_id"),
        account_name=account_name,
        session_uid=snapshot.get("session_uid", ""),
        summary=f"{account_name} shares a key with sanctioned account(s): {listed}",
        evidence={
            "signal": kind,
            "matches": hits,
            "observed_from_session": snapshot.get("session_uid", ""),
        },
    )


def _address_facts(snapshot: dict) -> dict:
    """The enrichment for this session's address, read back off the row.

    Detectors run after the write, so the row already carries whatever the
    lookup found. Reading it back is one query and avoids a second lookup
    disagreeing with what was stored.
    """
    from evennia.server.models import SessionRecord

    uid = snapshot.get("session_uid") or ""
    if not uid:
        return {}
    row = (
        SessionRecord.objects.filter(session_uid=uid)
        .values("ip", "asn", "asn_org", "country", "is_datacenter", "is_tor")
        .first()
    )
    return row or {}


def detect_datacenter(snapshot: dict):
    """This session came from a hosting provider rather than a consumer ISP.

    Not evidence of anything on its own -- a VPN is a normal thing for a normal
    person to use -- which is why it is severity 1 and why it is a flag. It
    matters as corroboration next to something else.
    """
    facts = _address_facts(snapshot)
    if not facts.get("is_datacenter"):
        return None
    account_name = (snapshot.get("account_name") or "").strip()
    kind = ModerationFlag.KIND_DATACENTER
    organisation = str(facts.get("asn_org") or "").strip() or f"AS{facts.get('asn')}"
    return raise_flag(
        kind=kind,
        dedupe_key=make_dedupe_key(kind, account_name, facts.get("asn")),
        severity=SEVERITY.get(kind, 1),
        account_id=snapshot.get("account_id"),
        account_name=account_name,
        session_uid=snapshot.get("session_uid", ""),
        summary=f"Connected from a hosting network: {organisation}",
        evidence={
            "signal": kind,
            "asn": facts.get("asn"),
            "asn_org": facts.get("asn_org"),
            "country": facts.get("country"),
            "cidr": snapshot.get("cidr"),
        },
    )


def detect_tor(snapshot: dict):
    """This session came from a published Tor exit."""
    facts = _address_facts(snapshot)
    if not facts.get("is_tor"):
        return None
    account_name = (snapshot.get("account_name") or "").strip()
    kind = ModerationFlag.KIND_TOR
    return raise_flag(
        kind=kind,
        dedupe_key=make_dedupe_key(kind, account_name, snapshot.get("cidr")),
        severity=SEVERITY.get(kind, 2),
        account_id=snapshot.get("account_id"),
        account_name=account_name,
        session_uid=snapshot.get("session_uid", ""),
        summary="Connected from a Tor exit address",
        evidence={
            "signal": kind,
            "cidr": snapshot.get("cidr"),
            "country": facts.get("country"),
        },
    )


def detect_signup_burst(snapshot: dict):
    """Several accounts created from one network inside a short window.

    Checked on first connection rather than at signup: registration knows the
    address it was reached from but has no history to compare it against, and
    the account has to arrive somewhere before "several accounts from one
    network" means anything. One account per network is the normal case; a
    household is two or three; a burst is what this names.
    """
    from evennia.accounts.models import AccountDB

    account_id = snapshot.get("account_id")
    cidr = str(snapshot.get("cidr") or "")
    account_name = (snapshot.get("account_name") or "").strip()
    if not account_id or not cidr or not account_name:
        return None

    window_hours = int(getattr(settings, "MODERATION_SIGNUP_BURST_HOURS", 24) or 24)
    threshold = int(getattr(settings, "MODERATION_SIGNUP_BURST_ACCOUNTS", 3) or 3)
    since = timezone.now() - timedelta(hours=window_hours)

    joined = AccountDB.objects.filter(pk=account_id).values_list("date_joined", flat=True).first()
    if not joined or joined < since:
        return None

    fresh_ids = set(
        AccountDB.objects.filter(date_joined__gte=since).values_list("id", flat=True)[:200]
    )
    if len(fresh_ids) < threshold:
        return None

    names = sorted(
        {
            str(name)
            for name in SessionRecord.objects.filter(cidr=cidr, account_id__in=fresh_ids)
            .exclude(account_name="")
            .values_list("account_name", flat=True)
            .distinct()[:50]
        }
    )
    if len(names) < threshold:
        return None

    kind = ModerationFlag.KIND_SIGNUP_BURST
    return raise_flag(
        kind=kind,
        dedupe_key=make_dedupe_key(kind, cidr),
        severity=SEVERITY.get(kind, 2),
        account_id=account_id,
        account_name=account_name,
        session_uid=snapshot.get("session_uid", ""),
        summary=(f"{len(names)} accounts created in the last {window_hours}h connect from {cidr}"),
        evidence={
            "signal": kind,
            "accounts": names,
            "cidr": cidr,
            "window_hours": window_hours,
        },
    )


def detect_identity_correlation(snapshot: dict):
    """Two weak signals together, pointing at a sanctioned account.

    Neither half is worth a flag alone. A shared ``/24`` is a household, a
    school, or one carrier-grade NAT; a shared client signature is two people
    who both use Mudlet. Either on its own would flag half the player base.

    Together, against an account that is currently blocked, they are the shape
    ban evasion actually has: the same person, on the same connection, with the
    same software, under a new name.

    Fires for an unauthenticated session too. That is the point of it -- a
    person who has been banned reconnects and sits at the login prompt, and
    ``detect_sanctioned_key_reuse`` cannot see them because it needs an account
    name. This one has the session, which is all the evidence there is at that
    moment.

    It flags. It never refuses: refusing at the door tells the evader which
    signal to change next.
    """

    cidr = str(snapshot.get("cidr") or "")
    if not cidr:
        return None

    signature = ""
    label = ""
    for field, name in (("telnet_sig", "client negotiation"), ("csessid", "browser session")):
        value = str(snapshot.get(field) or "")
        if value:
            signature, label, signature_field = value, name, field
            break
    if not signature:
        return None

    account_name = (snapshot.get("account_name") or "").strip()

    # Accounts that have used this network AND this signature. Two filters on
    # one query rather than two sets intersected: the pair is the signal.
    rows = _window(SessionRecord.objects.filter(cidr=cidr, **{signature_field: signature}))
    names = set(rows.exclude(account_name="").values_list("account_name", flat=True).distinct())
    names.discard(account_name)
    if not names:
        return None

    blocked = set(
        Sanction.objects.active()
        .filter(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value__in=[name.lower() for name in names],
            level__in=Sanction.BLOCKING_LEVELS,
        )
        .values_list("subject_value", flat=True)
    )
    hits = sorted(name for name in names if name.lower() in blocked)
    if not hits:
        return None

    kind = ModerationFlag.KIND_IDENTITY_CORRELATION
    who = account_name or "An unauthenticated session"
    return raise_flag(
        kind=kind,
        dedupe_key=make_dedupe_key(kind, account_name or cidr, *hits),
        severity=SEVERITY.get(kind, 0),
        account_id=snapshot.get("account_id"),
        account_name=account_name,
        session_uid=snapshot.get("session_uid", ""),
        summary=(
            f"{who} shares a network and a {label} with blocked account(s): " + ", ".join(hits)
        ),
        evidence={
            "signal": kind,
            "cidr": cidr,
            "matched_on": label,
            "accounts": hits,
            "observed_from_session": snapshot.get("session_uid", ""),
        },
    )


DETECTORS = (
    detect_sanctioned_key_reuse,
    detect_identity_correlation,
    detect_shared_device,
    detect_shared_csessid,
    detect_shared_client_on_network,
    detect_datacenter,
    detect_tor,
    detect_signup_burst,
)


def run_detectors(snapshot: dict) -> list:
    """
    Run every detector over one session snapshot.

    Each runs independently: one that raises does not stop the others, because a
    flag that was never written is a review that never happens.
    """
    if not getattr(settings, "MODERATION_DETECTION_ENABLED", True):
        return []

    raised = []
    for detector in DETECTORS:
        try:
            flag = detector(snapshot)
        except Exception:
            logger.log_trace("moderation detector %s failed" % detector.__name__)
            continue
        if flag is not None:
            raised.append(flag)
    return raised
