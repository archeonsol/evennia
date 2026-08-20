"""
Session capture.

Two phases, deliberately split:

``snapshot_session`` runs on the reactor thread and only reads in-memory session
state -- no ORM, no Attributes. ``persist_snapshot`` takes the resulting plain
dict and does the database work in a worker thread. The session object itself
never crosses the thread boundary.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json

from django.conf import settings
from django.utils import timezone

from evennia.utils import logger

# Capability flags that make up the client fingerprint. Screen dimensions are
# excluded on purpose: they change every time the player resizes the window.
#: Subnegotiations whose completion is decided by the client's telnet stack
#: rather than by the player. Screen size is deliberately absent: it completes
#: or not depending on the terminal, and it changes when a window is resized.
_NEG_SUBNEG_FLAGS = (
    "GMCP",
    "MCCP",
    "MSDP",
    "MSSP",
    "MXP",
    "MNES",
    "TTYPE",
)

_FP_BOOL_FLAGS = (
    "ANSI",
    "AUTORESIZE",
    "FORCEDENDLINE",
    "MCCP",
    "MNES",
    "MXP",
    "NOGOAHEAD",
    "NOPROMPTGOAHEAD",
    "OOB",
    "OOB_GMCP",
    "OOB_MSDP",
    "OSC_COLOR_PALETTE",
    "PROXY",
    "SCREENREADER",
    "TRUECOLOR",
    "TTYPE",
    "UTF-8",
    "XTERM256",
)

# Flags that carry no identifying value and only bloat the stored snapshot.
_FLAGS_SKIP = frozenset({"_saved_protocol_flags"})

_IPV4_PREFIX = 24
_IPV6_PREFIX = 64


def _salt() -> str:
    return str(getattr(settings, "MODERATION_HASH_SALT", "") or settings.SECRET_KEY)


def hash_value(value) -> str:
    """Salted SHA-256 of a value, or empty string for a falsy input."""
    if not value:
        return ""
    return hashlib.sha256((_salt() + str(value)).encode("utf-8")).hexdigest()


def derive_cidr(ip) -> str:
    """
    Network an address belongs to, as text.

    /24 for IPv4 and /64 for IPv6. Residential IPv6 rotates the low 64 bits on
    every lease, so a single /128 is worthless as an identity or a ban unit.
    """
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(str(ip))
    except ValueError:
        return ""
    prefix = _IPV4_PREFIX if addr.version == 4 else _IPV6_PREFIX
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def normalize_address(address):
    """Sessions carry either a plain address string or a (host, port) tuple."""
    if isinstance(address, (tuple, list)):
        address = address[0] if address else None
    if not address:
        return None
    address = str(address).strip()
    try:
        ipaddress.ip_address(address)
    except ValueError:
        return None
    return address


def _jsonable(value):
    """Coerce a protocol flag value into something JSONField can store."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sanitize_flags(flags) -> dict:
    if not isinstance(flags, dict):
        return {}
    return {str(k): _jsonable(v) for k, v in flags.items() if k not in _FLAGS_SKIP}


def client_fingerprint(flags: dict, protocol_key: str = "") -> str:
    """
    Stable hash over the negotiated client capability set.

    Same client build, same options, same terminal type produces the same value
    across reconnects and across IP changes.
    """
    if not flags:
        return ""
    parts = [
        ("PROTOCOL", str(protocol_key or "")),
        ("CLIENTNAME", str(flags.get("CLIENTNAME") or "")),
        ("TERM", str(flags.get("TERM") or "")),
        ("ENCODING", str(flags.get("ENCODING") or "")),
    ]
    for name in _FP_BOOL_FLAGS:
        parts.append((name, "1" if flags.get(name) else "0"))
    canonical = json.dumps(sorted(parts), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Headers a trusted reverse proxy may set to describe the TLS handshake it
#: terminated. Nothing else may set them: see :func:`tls_signature`.
TLS_HEADERS = ("x-tls-version", "x-tls-ciphers", "x-tls-curves", "x-tls-alpn")


def _is_grease(value: str) -> bool:
    """Return whether one hex code point is a GREASE placeholder.

    Chrome and Firefox insert random reserved values into the cipher and
    extension lists on every connection, to keep middleboxes from hard-coding
    what they see. They are ``0x0A0A``, ``0x1A1A`` ... ``0xFAFA``: both bytes
    equal, both nibbles ``A``.

    Not stripping them is the single reason JA3 aged badly. A fingerprint that
    includes GREASE changes on every Chrome connection, which makes it useless
    for recognising a returning client and worse than useless for staff, who
    would read "different fingerprint" as "different person".
    """

    text = str(value or "").strip().lower().removeprefix("0x")
    if len(text) != 4:
        return False
    return text[0] == text[2] and text[1] == text[3] == "a"


def _code_points(raw: str) -> list:
    """Return the non-GREASE code points in one colon-separated header."""

    points = []
    for item in str(raw or "").split(":"):
        item = item.strip()
        if not item or _is_grease(item):
            continue
        points.append(item.lower()[:8])
        if len(points) >= 64:
            break
    return points


def tls_signature(flags: dict) -> str:
    """Hash of the TLS handshake the client offered.

    Built from what a reverse proxy can report without any module: the
    protocol version, the client's offered cipher list, its supported curves,
    and the negotiated ALPN. Together these are most of what JA4's TLS half
    discriminates on.

    It is **not** JA4. JA4 also hashes the extension list, which no stock proxy
    variable exposes, so this is deliberately named for what it is rather than
    borrowing a name it does not earn.

    Two rules make it stable rather than noisy:

    GREASE values are stripped. They are random per connection by design, and
    keeping them is why JA3 stopped being useful.

    The lists are sorted. A client's cipher order is stable per build, but a
    proxy is free to report them in whatever order it read them, and a
    fingerprint that depends on the proxy's behaviour breaks when the proxy is
    upgraded.

    Empty unless a trusted proxy supplied the headers. A client can set any
    header it likes, so an untrusted one contributes nothing at all -- a forged
    fingerprint is worse than no fingerprint, because staff would believe it.
    """

    if not flags:
        return ""
    tls = flags.get("TLS_FP")
    if not isinstance(tls, dict) or not tls:
        return ""

    canonical = json.dumps(
        {
            "version": str(tls.get("x-tls-version") or "")[:16],
            "alpn": str(tls.get("x-tls-alpn") or "")[:16],
            "ciphers": sorted(_code_points(tls.get("x-tls-ciphers"))),
            "curves": sorted(_code_points(tls.get("x-tls-curves"))),
        },
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def header_order_signature(flags: dict) -> str:
    """Hash of which headers the client sent, and in what order.

    The cheap half of JA4H, and it needs no proxy at all: the handshake already
    arrives as an ordered list of header names, and the order a browser sends
    them in is a property of its build. A scripted client claiming to be Chrome
    rarely reproduces Chrome's order.

    Names only. The values are where the identifying detail an operator might
    misuse lives, and they are already hashed separately by
    :func:`http_fingerprint`.

    Hop-by-hop headers are dropped. A proxy adds, removes and reorders those,
    so including them would fingerprint the proxy rather than the client.
    """

    order = flags.get("HTTP_ORDER") if flags else None
    if not isinstance(order, list) or not order:
        return ""
    names = [str(name)[:64] for name in order if str(name).lower() not in _HOP_BY_HOP][:48]
    if not names:
        return ""
    canonical = json.dumps(names, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Headers a proxy owns rather than the client. Including them would fingerprint
#: the hop, and it would change the moment the proxy configuration did.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-host",
        "x-real-ip",
        "x-tls-version",
        "x-tls-ciphers",
        "x-tls-curves",
        "x-tls-alpn",
    }
)


def negotiation_signature(flags: dict, protocol_key: str = "") -> str:
    """Hash of the option negotiation the client library performed.

    Distinct from :func:`client_fingerprint`, and harder to change on purpose.
    That one includes ``CLIENTNAME``, ``TERM`` and ``ENCODING``, which a player
    sets: somebody evading a ban edits them in a settings dialog and the
    fingerprint moves.

    This reads only what the client's telnet stack chose to do -- which options
    it offered, in what order, and which subnegotiations it completed. A player
    cannot change that without changing client, and each dedicated MUD client
    negotiates a distinct and deterministic sequence.

    The order is kept rather than sorted. Sorting would throw away the part
    that identifies the library: two clients that support the same options
    still ask for them in their own order.

    Empty for a session that negotiated nothing, which is every web-client
    connection and any raw socket. An empty value is never a match: callers
    must not treat two blanks as the same client.
    """

    order = _neg_order(flags)
    if not order:
        return ""
    # Subnegotiations that actually completed, as a sorted set: whether the
    # client finished a subnegotiation is a property of its stack, but the
    # order it finished them in depends on the network.
    completed = sorted(name for name in _NEG_SUBNEG_FLAGS if flags and flags.get(name))
    canonical = json.dumps(
        {"protocol": str(protocol_key or ""), "order": order, "subneg": completed},
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def http_fingerprint(flags: dict) -> str:
    """
    Hash of the browser's handshake headers.

    Empty for telnet, and for any websocket session whose headers were not
    collected. Deliberately includes ``accept-encoding`` and the websocket
    extension list: those differ between browser families and between a real
    browser and a scripted client claiming to be one.
    """
    headers = flags.get("HTTP_FP") if flags else None
    if not isinstance(headers, dict) or not headers:
        return ""
    canonical = json.dumps(
        sorted((str(key).lower(), str(value)) for key, value in headers.items()),
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _user_agent(flags: dict) -> str:
    headers = flags.get("HTTP_FP") if flags else None
    if not isinstance(headers, dict):
        return ""
    return str(headers.get("user-agent") or "")[:512]


def _neg_order(flags: dict) -> list:
    value = flags.get("NEG_ORDER")
    if not isinstance(value, list):
        return []
    return [str(item)[:32] for item in value][:32]


def _neg_timing(flags: dict) -> dict:
    value = flags.get("NEG_TIMING_MS")
    if not isinstance(value, dict):
        return {}
    timings = {}
    for key, ms in list(value.items())[:32]:
        try:
            timings[str(key)[:32]] = round(float(ms), 1)
        except (TypeError, ValueError):
            continue
    return timings


def _screen_dimension(flags: dict, key: str):
    """SCREENWIDTH/SCREENHEIGHT are {windowID: value} maps; window 0 is the main one."""
    value = flags.get(key)
    if isinstance(value, dict):
        value = value.get(0, value.get("0"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def snapshot_session(session, *, reason=None) -> dict:
    """
    Read everything worth keeping off a live session.

    Reactor-thread safe: in-memory reads only. Never raises -- a missing field is
    always preferable to a broken login or disconnect.
    """
    snap = {}
    try:
        flags = sanitize_flags(getattr(session, "protocol_flags", None))
        protocol_key = str(getattr(session, "protocol_key", "") or "")
        address = normalize_address(getattr(session, "address", None))
        peer_ip = normalize_address(flags.get("PEER_IP"))
        account = getattr(session, "account", None)
        telemetry = getattr(session, "telemetry", None) or {}

        try:
            puppet = session.get_puppet()
            puppet_name = str(getattr(puppet, "key", "") or "")
        except Exception:
            puppet_name = ""

        snap = {
            "session_uid": str(getattr(session, "moderation_uid", "") or ""),
            "sessid": getattr(session, "sessid", None) or None,
            "protocol": protocol_key[:32],
            "account_id": getattr(account, "id", None),
            "account_name": str(getattr(account, "username", "") or "")[:255],
            "puppet_name": puppet_name[:255],
            "ip": address,
            "ip_hash": hash_value(address),
            "cidr": derive_cidr(address),
            "peer_ip": peer_ip,
            "xff_applied": bool(flags.get("XFF_APPLIED")),
            "xff_present": bool(flags.get("XFF_PRESENT")),
            "client_fp": client_fingerprint(flags, protocol_key),
            "client_name": str(flags.get("CLIENTNAME") or "")[:255],
            "term": str(flags.get("TERM") or "")[:64],
            "encoding": str(flags.get("ENCODING") or "")[:32],
            "screen_w": _screen_dimension(flags, "SCREENWIDTH"),
            "screen_h": _screen_dimension(flags, "SCREENHEIGHT"),
            "flags": flags,
            "neg_order": _neg_order(flags),
            "telnet_sig": negotiation_signature(flags, protocol_key),
            "tls_sig": tls_signature(flags),
            "http_order_fp": header_order_signature(flags),
            "neg_timing_ms": _neg_timing(flags),
            "csessid": str(getattr(session, "csessid", "") or "")[:64],
            "device_token": str(flags.get("DEVICE_TOKEN") or "")[:64],
            "http_fp": http_fingerprint(flags),
            "user_agent": _user_agent(flags),
            "command_count": int(telemetry.get("command_count") or 0),
            "connected_at": getattr(session, "moderation_connected_at", None) or timezone.now(),
        }
        if reason is not None:
            snap["disconnect_reason"] = str(reason or "")[:255]
    except Exception:
        logger.log_trace("moderation.snapshot_session failed")
    return snap


_PERSIST_FIELDS = (
    "sessid",
    "protocol",
    "account_name",
    "puppet_name",
    "ip",
    "ip_hash",
    "cidr",
    "peer_ip",
    "xff_applied",
    "xff_present",
    "client_fp",
    "client_name",
    "term",
    "encoding",
    "screen_w",
    "screen_h",
    "flags",
    "neg_order",
    "telnet_sig",
    "tls_sig",
    "http_order_fp",
    "neg_timing_ms",
    "csessid",
    "device_token",
    "http_fp",
    "user_agent",
    "command_count",
    "connected_at",
)


def enrichment(address) -> dict:
    """ASN, country and list membership for one address. Never raises."""
    try:
        from evennia.moderation.enrich import describe

        return describe(address)
    except Exception:
        logger.log_trace("moderation.enrichment failed")
        return {}


def persist_snapshot(snap: dict, phase: str = "login") -> None:
    """
    Write a snapshot to the database. Runs in a worker thread.

    ``phase`` is ``"login"`` (create or refresh the row) or ``"disconnect"``
    (stamp the end of the session). A session that never authenticated has no
    login write, so disconnect creates the row itself.
    """
    uid = snap.get("session_uid")
    if not uid:
        return
    try:
        from evennia.server.models import SessionRecord

        fields = {key: snap[key] for key in _PERSIST_FIELDS if key in snap}
        fields["account_id"] = snap.get("account_id")
        # Address intelligence is a local database read, so it belongs on this
        # side of the thread boundary with the rest of the blocking work. An
        # absent database contributes no keys rather than null ones.
        fields.update(enrichment(snap.get("ip")))

        now = timezone.now()
        if phase == "login":
            fields["login_at"] = now
        else:
            fields["disconnected_at"] = now
            fields["disconnect_reason"] = snap.get("disconnect_reason", "")

        SessionRecord.objects.update_or_create(session_uid=uid, defaults=fields)
    except Exception:
        logger.log_trace("moderation.persist_snapshot failed (phase=%s)" % phase)
        return

    if phase != "login":
        # Detectors compare this session against history, so they run once the
        # row is final. Already in a worker thread; the queries stay off the
        # reactor.
        try:
            from evennia.moderation.detect import run_detectors

            run_detectors(snap)
        except Exception:
            logger.log_trace("moderation.persist_snapshot detectors failed")


def record_session(session, *, phase: str, reason=None) -> None:
    """Snapshot on the reactor, then persist in a worker thread. Never raises."""
    try:
        if not getattr(settings, "MODERATION_SESSION_CAPTURE_ENABLED", True):
            return
        snap = snapshot_session(session, reason=reason)
        if not snap.get("session_uid"):
            return
        from evennia.utils import defer

        defer.background(
            persist_snapshot,
            snap,
            phase,
            on_error=lambda failure: logger.log_err(
                "moderation.record_session(%s) failed:\n%s" % (phase, failure.getTraceback())
            ),
        )
    except Exception:
        logger.log_trace("moderation.record_session failed (phase=%s)" % phase)
