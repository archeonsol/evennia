"""Message-volume observation.

A text game's flood is text, not TCP. The connection limiter counts sockets and
cannot see a single logged-in account sending three hundred tells a minute.

**This never blocks anything.** It raises a flag, which is the same rule the
rest of the substrate follows: detection writes observations, a person decides.
That is not caution for its own sake. A message limit that blocks is a limit
that will eventually refuse a real player mid-scene -- a fight round, an
auction, a crowded room, somebody pasting a long pose in pieces -- and the cost
of being wrong there is much higher than the cost of a flag nobody reads.

So the thresholds are set where normal play does not reach them, and the only
consequence of crossing one is that a staff member sees a row.

It is **off by default**. A game turns it on after watching its own numbers,
because "high" is a property of the game rather than of the engine.

Counters live in the process that calls this, in memory, with no database read
on the message path. Crossing a threshold writes one flag and then stays quiet
for a cooldown, so a sustained flood produces one row rather than thousands.

"""

from __future__ import annotations

import time
from collections import OrderedDict, deque

from django.conf import settings

from evennia.utils import logger

#: Accounts tracked at once. Past this the least recently seen is dropped, so
#: memory is bounded no matter how many accounts connect.
MAX_KEYS = 4096

#: Seconds after a flag before the same account can raise another one. A
#: sustained flood is one event, not one per message.
COOLDOWN = 900.0


def _setting(name, default):
    """Return one setting, treating ``None`` as absent."""

    value = getattr(settings, name, default)
    return default if value is None else value


class MessageRateObserver:
    """Counts messages per account and flags a burst. Refuses nothing."""

    def __init__(self):
        self._windows = OrderedDict()
        self._last_flagged = {}

    def clear(self) -> None:
        """Forget every counter. Used by tests and by a manual reset."""

        self._windows.clear()
        self._last_flagged.clear()

    def stats(self) -> dict:
        """Return how much this observer is currently holding."""

        return {"accounts": len(self._windows), "flagged": len(self._last_flagged)}

    def note(self, account_id, account_name="", kind="message", session_uid="", *, now=None):
        """Record one message, and flag a burst if this crosses the threshold.

        Never raises and never refuses. The caller sends the message either
        way; this only counts it.

        Args:
            account_id: Account the message came from.
            account_name: Display name, for the flag.
            kind: What sort of message, for the flag's evidence.
            session_uid: Session the message came from, if known.
            now: Monotonic clock, for tests.

        Returns:
            bool: Whether this call raised a flag. Callers may ignore it; it is
            returned for tests and for a status command, never to gate a send.
        """

        try:
            return self._note(account_id, account_name, kind, session_uid, now)
        except Exception:  # noqa: BLE001 - observation must never break a send
            logger.log_trace("moderation.messagerate failed")
            return False

    def _note(self, account_id, account_name, kind, session_uid, now) -> bool:
        if not _setting("MODERATION_MESSAGE_RATE_ENABLED", False):
            return False
        limit = int(_setting("MODERATION_MESSAGE_RATE_LIMIT", 120))
        window = float(_setting("MODERATION_MESSAGE_RATE_WINDOW", 60))
        if limit <= 0 or window <= 0 or not account_id:
            return False

        key = int(account_id)
        now = time.monotonic() if now is None else now

        timestamps = self._windows.get(key)
        if timestamps is None:
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

        last = self._last_flagged.get(key)
        if last is not None and now - last < COOLDOWN:
            return False
        self._last_flagged[key] = now
        return self._flag(key, account_name, kind, session_uid, len(timestamps), window)

    def _flag(self, account_id, account_name, kind, session_uid, count, window) -> bool:
        """Write one flag for a burst. Returns whether it was written."""

        from evennia.moderation.flags import raise_flag
        from evennia.server.models import ModerationFlag

        name = str(account_name or "")
        try:
            raise_flag(
                kind=ModerationFlag.KIND_MESSAGE_BURST,
                severity=1,
                account_id=account_id,
                account_name=name,
                session_uid=str(session_uid or ""),
                summary=(f"{name or account_id} sent {count} messages in {int(window)} seconds."),
                evidence={
                    "signal": ModerationFlag.KIND_MESSAGE_BURST,
                    "message_kind": str(kind or "message"),
                    "count": count,
                    "window_seconds": int(window),
                },
                dedupe_key=f"{ModerationFlag.KIND_MESSAGE_BURST}:{account_id}",
            )
        except Exception:  # noqa: BLE001 - a flag that cannot be written is not a fault
            logger.log_trace("moderation.messagerate could not write a flag")
            return False
        return True

    def _evict_if_full(self) -> None:
        """Drop the least recently seen account when the table is full."""

        max_keys = int(_setting("MODERATION_MESSAGE_RATE_MAX_KEYS", MAX_KEYS))
        while max_keys > 0 and len(self._windows) > max_keys:
            evicted, _ = self._windows.popitem(last=False)
            self._last_flagged.pop(evicted, None)


#: Process-wide observer. One per process, like the connection limiter.
OBSERVER = MessageRateObserver()


def note_message(account_id, account_name="", kind="message", session_uid="") -> bool:
    """Record one message from one account.

    The function a game calls. Safe to call on every send: it does no database
    work unless a threshold is crossed, and it never refuses anything.
    """

    return OBSERVER.note(account_id, account_name, kind, session_uid)
