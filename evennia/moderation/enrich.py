"""
Network intelligence: what an address is, without asking anyone at connect time.

Three questions, three local answers:

**Which network operator?** A MaxMind GeoLite2 ASN database, read from disk.
Microseconds per lookup, no network call, no third-party service holding a log
of every address your players connect from.

**Is that operator a hosting provider?** A denylist of hosting ASNs, refreshed
by a scheduled job. A denylist rather than a residential allowlist because the
set of hosting providers is small and enumerable and the set of consumer ISPs
is neither.

**Is the address a Tor exit?** The Tor project publishes the list; a scheduled
job stores it.

Everything degrades to "not looked up". A missing database, an unreadable file,
an absent ``maxminddb`` package and an empty list all produce ``None``, never a
guess and never an exception. The column means *not looked up yet*, which is a
different thing from *clean*, and a login must never fail because a lookup did.

Both lists live in ``ServerConfig``, so they survive a restart and need no cache
server. They are read through a process-local cache stamped with the version
the lists were last written at, so a refresh invalidates every process without
any of them polling.
"""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.utils import timezone

from evennia.utils import logger

# ServerConfig keys. Versioned so a process can tell its cache is stale without
# reading the lists themselves back out.
KEY_DATACENTER_ASNS = "moderation_datacenter_asns"
KEY_TOR_EXITS = "moderation_tor_exits"
KEY_DISPOSABLE_DOMAINS = "moderation_disposable_domains"
KEY_VERSION = "moderation_network_lists_version"
KEY_UPDATED = "moderation_network_lists_updated"

_readers_loaded = False
_asn_reader = None
_country_reader = None

_cache_version = None
_datacenter_asns: frozenset[int] = frozenset()
_tor_exits: frozenset[str] = frozenset()
_disposable_domains: frozenset[str] = frozenset()


def _setting(name, default=""):
    return getattr(settings, name, default)


def _open_readers():
    """Open the mmdb readers once per process. Never raises."""
    global _readers_loaded, _asn_reader, _country_reader
    if _readers_loaded:
        return _asn_reader, _country_reader
    _readers_loaded = True

    asn_path = str(_setting("MODERATION_GEOIP_ASN_DB") or "")
    country_path = str(_setting("MODERATION_GEOIP_COUNTRY_DB") or "")
    if not asn_path and not country_path:
        return None, None

    try:
        import maxminddb
    except ImportError:
        # The databases are configured but the reader is not installed. Say so
        # once: silently recording nothing looks identical to a clean grid.
        logger.log_warn(
            "moderation: MODERATION_GEOIP_* is set but the maxminddb package "
            "is not installed. Address enrichment is off."
        )
        return None, None

    def _open(path):
        if not path:
            return None
        try:
            return maxminddb.open_database(path)
        except (OSError, ValueError):
            logger.log_warn(f"moderation: cannot open GeoLite2 database at {path!r}.")
            return None

    _asn_reader = _open(asn_path)
    _country_reader = _open(country_path)
    return _asn_reader, _country_reader


def close_readers() -> None:
    """Release the mmdb handles. Used by tests and at shutdown."""
    global _readers_loaded, _asn_reader, _country_reader
    for reader in (_asn_reader, _country_reader):
        try:
            if reader is not None:
                reader.close()
        except Exception:
            pass
    _readers_loaded = False
    _asn_reader = None
    _country_reader = None


def _lists():
    """The stored lists, from the process cache or from ``ServerConfig``."""
    global _cache_version, _datacenter_asns, _tor_exits, _disposable_domains
    from evennia.server.models import ServerConfig

    current = (_datacenter_asns, _tor_exits, _disposable_domains)
    try:
        version = ServerConfig.objects.conf(KEY_VERSION, default=0)
    except Exception:
        return current
    if version == _cache_version:
        return current

    try:
        asns = ServerConfig.objects.conf(KEY_DATACENTER_ASNS, default=()) or ()
        exits = ServerConfig.objects.conf(KEY_TOR_EXITS, default=()) or ()
        domains = ServerConfig.objects.conf(KEY_DISPOSABLE_DOMAINS, default=()) or ()
    except Exception:
        logger.log_trace("moderation: network lists could not be read")
        return current

    _datacenter_asns = frozenset(int(asn) for asn in asns)
    _tor_exits = frozenset(str(address) for address in exits)
    _disposable_domains = frozenset(str(domain).lower() for domain in domains)
    _cache_version = version
    return _datacenter_asns, _tor_exits, _disposable_domains


def disposable_domain_set() -> frozenset:
    """Domains a signup address is not worth verifying against."""
    return _lists()[2]


def store_lists(*, datacenter_asns=None, tor_exits=None, disposable_domains=None) -> dict:
    """
    Replace one or both lists and invalidate every process's cache.

    Returns the resulting counts. Callers are scheduled jobs; nothing on a
    connection path writes here.
    """
    from evennia.server.models import ServerConfig

    if datacenter_asns is not None:
        cleaned = sorted({int(asn) for asn in datacenter_asns if str(asn).strip()})
        ServerConfig.objects.conf(KEY_DATACENTER_ASNS, tuple(cleaned))
    if tor_exits is not None:
        cleaned_exits = sorted(
            {str(address).strip() for address in tor_exits if str(address).strip()}
        )
        ServerConfig.objects.conf(KEY_TOR_EXITS, tuple(cleaned_exits))
    if disposable_domains is not None:
        cleaned_domains = sorted(
            {str(domain).strip().lower() for domain in disposable_domains if str(domain).strip()}
        )
        ServerConfig.objects.conf(KEY_DISPOSABLE_DOMAINS, tuple(cleaned_domains))

    version = int(ServerConfig.objects.conf(KEY_VERSION, default=0) or 0) + 1
    ServerConfig.objects.conf(KEY_VERSION, version)
    ServerConfig.objects.conf(KEY_UPDATED, timezone.now().isoformat())
    return list_state()


def list_state() -> dict:
    """Counts and freshness, for the boot advisory and the staff surface."""
    from evennia.server.models import ServerConfig

    try:
        asns = ServerConfig.objects.conf(KEY_DATACENTER_ASNS, default=()) or ()
        exits = ServerConfig.objects.conf(KEY_TOR_EXITS, default=()) or ()
        domains = ServerConfig.objects.conf(KEY_DISPOSABLE_DOMAINS, default=()) or ()
        updated = ServerConfig.objects.conf(KEY_UPDATED, default="") or ""
    except Exception:
        return {
            "datacenter_asns": 0,
            "tor_exits": 0,
            "disposable_domains": 0,
            "updated": "",
            "stale": True,
        }
    return {
        "datacenter_asns": len(asns),
        "tor_exits": len(exits),
        "disposable_domains": len(domains),
        "updated": str(updated),
        "stale": _is_stale(str(updated)),
    }


def _is_stale(updated: str) -> bool:
    """Whether the lists are old enough that they should not be trusted."""
    from datetime import datetime, timedelta

    if not updated:
        return True
    try:
        stamp = datetime.fromisoformat(updated)
    except ValueError:
        return True
    max_age = int(_setting("MODERATION_NETWORK_LIST_MAX_AGE_DAYS", 45) or 45)
    return timezone.now() - stamp > timedelta(days=max_age)


def _mmdb_asn(address: str):
    """``(asn, organisation)`` from the ASN database, or ``(None, "")``."""
    asn_reader, _country = _open_readers()
    if asn_reader is None:
        return None, ""
    try:
        record = asn_reader.get(address)
    except (ValueError, TypeError):
        return None, ""
    if not isinstance(record, dict):
        return None, ""
    number = record.get("autonomous_system_number")
    organisation = record.get("autonomous_system_organization") or ""
    try:
        return int(number), str(organisation)[:255]
    except (TypeError, ValueError):
        return None, str(organisation)[:255]


def _mmdb_country(address: str) -> str:
    """ISO country code from the country database, or empty."""
    _asn, country_reader = _open_readers()
    if country_reader is None:
        return ""
    try:
        record = country_reader.get(address)
    except (ValueError, TypeError):
        return ""
    if not isinstance(record, dict):
        return ""
    country = record.get("country") or record.get("registered_country") or {}
    return str(country.get("iso_code") or "")[:8]


def describe(address) -> dict:
    """
    What is known about one address.

    Keys are exactly the enrichment columns on ``SessionRecord``. A key is
    omitted when nothing was looked up, so a caller merging this into a field
    dict never overwrites a previous answer with a null one.
    """
    text = str(address or "").strip()
    if not text:
        return {}
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return {}
    if parsed.is_private or parsed.is_loopback:
        # A private address has no operator to name and no list to appear on.
        return {}

    described = {}
    try:
        asn, organisation = _mmdb_asn(text)
        country = _mmdb_country(text)
    except Exception:
        logger.log_trace("moderation.describe failed")
        return {}

    if asn is not None:
        described["asn"] = asn
        described["is_datacenter"] = asn in _lists()[0]
    if organisation:
        described["asn_org"] = organisation
    if country:
        described["country"] = country

    tor_exits = _lists()[1]
    if tor_exits:
        described["is_tor"] = text in tor_exits
    return described
