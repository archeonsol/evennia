"""
Connection rate limiting at the Portal door.

A flood is refused before a session object exists, on the same call that checks
the blocklist. Counters live in this process and nowhere else: the Portal is one
process, so a shared store would buy no accuracy, and a cache round trip per
connection would add I/O to the reactor at exactly the moment the reactor is
under the most pressure. A dict lookup and a deque append cost microseconds and
touch no socket.

The limit is per network (``/24`` or ``/64``), not per address. One address per
connection is what an ordinary flood generator varies first, and the network is
already the unit every other part of moderation counts in.

Two deliberate exemptions:

**Private and loopback addresses are never limited.** When the reverse proxy is
on loopback and its forwarded address is not trusted, every websocket connection
arrives wearing the proxy's address. Limiting that key would refuse the entire
web client because of a configuration mistake. See ``checks.py`` for the
advisory that reports the mistake itself.

**Nothing is written to the database from here.** A flood that reached the door
would otherwise become a flood of rows.

Memory is bounded twice over: each network holds at most ``limit + 1``
timestamps, and the map holds a fixed number of networks, evicting the least
recently seen. Nothing here grows with uptime.
"""

from __future__ import annotations

import ipaddress
import time
from collections import OrderedDict, deque

from django.conf import settings

from evennia.moderation.capture import derive_cidr, normalize_address

#: Prefix an IPv6 site is counted against, alongside its /64. A customer
#: assignment is a /56 or a /48, so /48 is the widest unit that is still one
#: subscriber rather than a whole carrier.
_IPV6_SITE_PREFIX = 48
from evennia.utils import logger

REFUSAL_TEXT = "Too many connections from your network. Wait a minute and try again.\r\n"
REFUSAL_BYTES = REFUSAL_TEXT.encode("utf-8")

# How often one network may be reported to the log while it stays over the
# limit. A flood that trips the limiter must not also flood the log.
_LOG_INTERVAL = 60.0


def _setting(name, default):
    value = getattr(settings, name, default)
    return default if value is None else value


class ConnectionRateLimiter:
    """Sliding-window connection counter, keyed by network."""

    def __init__(self):
        self._windows = OrderedDict()
        self._last_logged = {}

    def clear(self) -> None:
        """Forget every counter. Used by tests and by a manual reset."""
        self._windows.clear()
        self._last_logged.clear()

    def stats(self) -> dict:
        """Networks currently tracked, for a staff or diagnostic read."""
        return {
            "networks": len(self._windows),
            "connections": sum(len(window) for window in self._windows.values()),
        }

    def check(self, address, *, now=None) -> bool:
        """
        Record one connection attempt and say whether it is over the limit.

        Returns ``True`` when the connection should be refused. Every failure
        path returns ``False``: a limiter that cannot decide must not be the
        reason a player cannot log in.
        """
        try:
            return self._check(address, now)
        except Exception:
            logger.log_trace("moderation.ratelimit check failed")
            return False

    def _check(self, address, now) -> bool:
        if not _setting("MODERATION_CONNECT_RATE_ENABLED", True):
            return False
        limit = int(_setting("MODERATION_CONNECT_RATE_LIMIT", 20))
        window = float(_setting("MODERATION_CONNECT_RATE_WINDOW", 60))
        if limit <= 0 or window <= 0:
            return False

        normalized = normalize_address(address)
        if not normalized or self._exempt(normalized):
            return False

        now = time.monotonic() if now is None else now
        for key, bucket_limit in self._buckets(normalized, limit):
            if self._over(key, bucket_limit, window, now):
                return True
        return False

    def _buckets(self, address: str, limit: int):
        """Yield every ``(key, limit)`` this address is counted against.

        IPv4 gets one bucket, its /24, and that is the whole story: an attacker
        who wants a second /24 has to rent one.

        IPv6 gets two. A residential customer is handed a /56 or a /48, not a
        single /64, so one person routinely holds between 256 and 65,536
        distinct /64 keys. A limit that counts only /64 is a limit that
        multiplies by that number for anybody who reads their own address
        assignment, which is not a sophisticated attack.

        The wider bucket carries a wider allowance, set by
        ``MODERATION_CONNECT_RATE_SITE_FACTOR``. It has to be loose enough that
        a genuinely shared site -- a university, a large household behind one
        prefix -- is not refused during a normal evening, and tight enough that
        one prefix cannot open unlimited connections by walking its own
        subnets.

        Args:
            address: Normalized client address.
            limit: The configured per-network limit.

        Yields:
            tuple: A counting key and the limit that applies to it.
        """

        narrow = derive_cidr(address)
        if narrow:
            yield narrow, limit

        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return
        if parsed.version != 6:
            return

        factor = float(_setting("MODERATION_CONNECT_RATE_SITE_FACTOR", 8))
        if factor <= 0:
            return
        try:
            site = str(ipaddress.ip_network(f"{address}/{_IPV6_SITE_PREFIX}", strict=False))
        except ValueError:
            return
        yield site, max(limit, int(limit * factor))

    def _over(self, key, limit, window, now) -> bool:
        """Return whether one key has passed its limit inside the window."""

        timestamps = self._windows.get(key)
        if timestamps is None:
            # maxlen caps a single network's memory. One slot past the limit is
            # enough to answer "more than limit within the window".
            timestamps = deque(maxlen=limit + 1)
            self._windows[key] = timestamps
            self._evict_if_full()
        else:
            self._windows.move_to_end(key)

        cutoff = now - window
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

        timestamps.append(now)
        if len(timestamps) <= limit:
            return False

        self._log_once(key, len(timestamps), window, now)
        return True

    def _exempt(self, address: str) -> bool:
        """Loopback and private addresses are counted by nobody."""
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return True
        return parsed.is_loopback or parsed.is_private

    def _evict_if_full(self) -> None:
        max_keys = int(_setting("MODERATION_CONNECT_RATE_MAX_KEYS", 4096))
        while max_keys > 0 and len(self._windows) > max_keys:
            evicted, _ = self._windows.popitem(last=False)
            self._last_logged.pop(evicted, None)

    def _log_once(self, key: str, count: int, window: float, now: float) -> None:
        last = self._last_logged.get(key, 0.0)
        if now - last < _LOG_INTERVAL:
            return
        self._last_logged[key] = now
        logger.log_warn(
            "moderation: refusing connections from %s (%s attempts in %ss)"
            % (key, count, int(window))
        )


LIMITER = ConnectionRateLimiter()


def rate_limited(address) -> bool:
    """Whether this connection is over the per-network rate limit."""
    return LIMITER.check(address)
