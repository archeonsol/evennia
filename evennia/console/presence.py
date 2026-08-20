"""Who else has the console open, and what they have open.

Two staff working the same flag queue is the normal case, not the edge case.
Without this, the second person to open a record finds out about the first when
their write is rejected as a conflict -- or worse, is not.

**This writes nothing to the database.** A heartbeat per open console per
interval would be a steady write load whose only purpose is to say "still
here", which is exactly the kind of cost ``performance.md`` argues against. It
lives in the cache instead, with a time to live slightly longer than the
heartbeat interval, so a closed tab disappears on its own and nothing has to
clean up after it.

The consequence is worth stating plainly: presence is per cache. With the
default local-memory cache it covers one web process, which is what a normal
deployment runs. A deployment with several web processes and no shared cache
sees each process's operators separately, and the panel says so rather than
implying it saw everybody.

"""

from __future__ import annotations

import time

from django.core.cache import cache as default_cache
from django.core.cache import caches

#: Key holding the set of active operator ids.
ROSTER_KEY = "console:presence:roster"

#: Seconds an entry survives without a heartbeat. Longer than the client's
#: interval so one missed beat does not make somebody vanish mid-edit.
TTL = 90

#: Seconds the client should wait between heartbeats. Sent to the client so the
#: two numbers cannot drift apart in separate files.
HEARTBEAT = 30

#: Operators reported at once.
MAX_PRESENT = 50


def _cache():
    """Return the cache presence lives in."""

    try:
        return caches["default"]
    except Exception:  # noqa: BLE001 - a missing alias must not break the console
        return default_cache


def _key(actor_id) -> str:
    """Return the cache key for one operator."""

    return f"console:presence:{int(actor_id)}"


def touch(actor_id, actor_name="", panel="", record="") -> dict:
    """Record that one operator is here, and on what.

    Args:
        actor_id: Acting account id.
        actor_name: Display name.
        panel: Panel key they have open.
        record: Record reference they have open, if any.

    Returns:
        dict: The entry stored.
    """

    entry = {
        "actor_id": int(actor_id),
        "actor_name": str(actor_name or "")[:120],
        "panel": str(panel or "")[:64],
        "record": str(record or "")[:160],
        "seen": time.time(),
    }
    cache = _cache()
    cache.set(_key(actor_id), entry, TTL)

    # The roster is a hint, not the source of truth. Entries expire on their
    # own; this only avoids scanning the whole cache to find them, and a stale
    # id in it costs one miss rather than a wrong answer.
    roster = set(cache.get(ROSTER_KEY) or ())
    roster.add(int(actor_id))
    cache.set(ROSTER_KEY, sorted(roster)[-500:], TTL * 4)
    return entry


def present(exclude=None) -> list:
    """Return the operators currently here.

    Args:
        exclude: Actor id to leave out, normally the caller.

    Returns:
        list: Entries, most recently seen first.
    """

    cache = _cache()
    roster = list(cache.get(ROSTER_KEY) or ())[:MAX_PRESENT]
    found = []
    for actor_id in roster:
        if exclude is not None and int(actor_id) == int(exclude):
            continue
        entry = cache.get(_key(actor_id))
        if entry:
            found.append(entry)
    found.sort(key=lambda entry: entry.get("seen", 0), reverse=True)
    return found


def on_record(record: str, exclude=None) -> list:
    """Return the operators who have one record open.

    This is what turns presence into a warning instead of a curiosity: it
    answers "is somebody else in this row right now" before a write, rather
    than after one is rejected.

    Args:
        record: Record reference, as the panels build it.
        exclude: Actor id to leave out.

    Returns:
        list: Entries on that record.
    """

    wanted = str(record or "")
    if not wanted:
        return []
    return [entry for entry in present(exclude=exclude) if entry.get("record") == wanted]


def leave(actor_id) -> None:
    """Drop one operator's entry immediately.

    Called when a console is closed deliberately. Without it the entry lingers
    for the time to live, and somebody who left five seconds ago still reads as
    editing the row you are about to edit.
    """

    try:
        _cache().delete(_key(actor_id))
    except Exception:  # noqa: BLE001 - leaving must never fail loudly
        pass
