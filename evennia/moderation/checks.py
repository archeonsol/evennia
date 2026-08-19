"""
Address-provenance checks.

Every address-based moderation signal -- bans, alt matching, rate limits -- is
worthless if the recorded address is the reverse proxy rather than the player.
The websocket protocol only rewrites ``X-Forwarded-For`` when the TCP peer is
listed in ``settings.UPSTREAM_IPS`` (default ``["127.0.0.1"]``), and silently
keeps the proxy address otherwise.

These checks make that failure loud instead of invisible: one advisory at boot
from configuration alone, and one over recorded sessions once there is data.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings

from evennia.utils import logger

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def _websocket_host() -> str:
    url = getattr(settings, "WEBSOCKET_CLIENT_URL", "") or ""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def config_warning() -> str:
    """
    Advisory string when the proxy configuration looks unsafe, else empty.

    Not proof of a fault: a reverse proxy on loopback with the default
    ``UPSTREAM_IPS`` is correct. It flags the combination worth checking.
    """
    host = _websocket_host()
    if host in _LOCAL_HOSTS:
        return ""
    upstream = list(getattr(settings, "UPSTREAM_IPS", []) or [])
    if upstream and set(upstream) - {"127.0.0.1", "::1"}:
        return ""
    return (
        "moderation: WEBSOCKET_CLIENT_URL points at %r but UPSTREAM_IPS is %r. "
        "If the reverse proxy is not on loopback, every websocket session records "
        "the proxy address instead of the player address. Add the proxy address to "
        "UPSTREAM_IPS. Behind Cloudflare, trust CF-Connecting-IP instead of "
        "X-Forwarded-For. Run the recorded-session check after the next restart to "
        "confirm." % (host, upstream)
    )


def check_recorded_sessions(limit: int = 500) -> dict:
    """
    Inspect recent websocket rows for un-unwound proxy addresses.

    Returns counts plus ``verdict``:
      ``ok``        -- forwarded addresses were trusted and applied
      ``untrusted`` -- a proxy sent X-Forwarded-For that UPSTREAM_IPS rejected;
                       those rows hold the proxy address, not the player address
      ``direct``    -- no proxy headers seen at all
      ``no_data``   -- nothing recorded yet
    """
    from evennia.server.models import SessionRecord

    rows = list(
        SessionRecord.objects.filter(protocol="websocket")
        .order_by("-connected_at")
        .values("ip", "peer_ip", "xff_applied", "xff_present")[:limit]
    )
    if not rows:
        return {"verdict": "no_data", "total": 0, "applied": 0, "untrusted": 0, "distinct_ips": 0}

    applied = sum(1 for row in rows if row["xff_applied"])
    untrusted = sum(1 for row in rows if row["xff_present"] and not row["xff_applied"])
    distinct_ips = len({row["ip"] for row in rows if row["ip"]})

    if untrusted:
        verdict = "untrusted"
    elif applied:
        verdict = "ok"
    else:
        verdict = "direct"

    return {
        "verdict": verdict,
        "total": len(rows),
        "applied": applied,
        "untrusted": untrusted,
        "distinct_ips": distinct_ips,
    }


def report_lines(limit: int = 500) -> list:
    """Human-readable summary of both checks, for a staff command or the log."""
    lines = ["|wAddress provenance|n"]
    warning = config_warning()
    lines.append("  config: " + (warning if warning else "no configuration concern"))

    result = check_recorded_sessions(limit=limit)
    verdict = result["verdict"]
    if verdict == "no_data":
        lines.append("  sessions: no websocket sessions recorded yet")
        return lines

    lines.append(
        "  sessions: %s recent, %s forwarded and trusted, %s forwarded and rejected, "
        "%s distinct addresses"
        % (result["total"], result["applied"], result["untrusted"], result["distinct_ips"])
    )
    if verdict == "untrusted":
        lines.append(
            "  |rFAULT|n: a proxy is sending X-Forwarded-For that UPSTREAM_IPS does not "
            "trust. Web addresses on those rows are the proxy address. Fix UPSTREAM_IPS "
            "before issuing any address sanction."
        )
    elif verdict == "direct" and result["distinct_ips"] <= 1:
        lines.append(
            "  |rFAULT|n: every websocket session shares one address and no proxy header "
            "was seen. The proxy is not forwarding the client address."
        )
    lines.extend(enrichment_lines())
    return lines


def enrichment_lines() -> list:
    """State of the address-intelligence databases and lists."""
    import os

    from evennia.moderation.enrich import list_state

    lines = ["|wAddress intelligence|n"]
    configured = [
        path
        for path in (
            getattr(settings, "MODERATION_GEOIP_ASN_DB", ""),
            getattr(settings, "MODERATION_GEOIP_COUNTRY_DB", ""),
        )
        if path
    ]
    if not configured:
        lines.append("  geoip: no database configured. ASN and country are not recorded.")
    else:
        try:
            import maxminddb  # noqa: F401

            reader = True
        except ImportError:
            reader = False
        missing = [path for path in configured if not os.path.exists(path)]
        if not reader:
            lines.append(
                "  note: a GeoLite2 database path is set but maxminddb is not installed. "
                "ASN and country are not recorded."
            )
        elif missing:
            lines.append(
                "  note: GeoLite2 file not found: %s. Run geoipupdate." % ", ".join(missing)
            )
        else:
            lines.append("  geoip: databases present, reader installed")

    try:
        state = list_state()
    except Exception:
        return lines
    lines.append(
        "  lists: %s datacenter ASNs, %s tor exits, %s disposable domains, updated %s"
        % (
            state["datacenter_asns"],
            state["tor_exits"],
            state.get("disposable_domains", 0),
            state["updated"] or "never",
        )
    )
    if state["stale"]:
        lines.append(
            "  note: lists are stale or were never fetched. They only add flags, so "
            "nothing is blocked. Run the moderation_network_lists job to refresh them."
        )
    return lines


def warn_at_startup() -> None:
    """Log the configuration advisory at server start. Never raises."""
    try:
        warning = config_warning()
        if warning:
            logger.log_warn(warning)
    except Exception:
        logger.log_trace("moderation.warn_at_startup failed")
