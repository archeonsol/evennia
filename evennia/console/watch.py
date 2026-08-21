"""Watching one session's traffic from the console.

Surveillance of a player by a staff member, built so that the record of it is
harder to lose than the thing itself.

Three properties decide whether this is an operations tool or something worse,
and each one is a decision rather than an accident:

**The player is told nothing.** There is no in-game notice, no flag on the
character, no entry anywhere a player can read. This is deliberate and it is
what makes the audit trail load-bearing: the only people who can see that a
watch happened are other console users, so that record has to be complete.

**The audit row is written when output is delivered, not when the button is
pressed.** An earlier version of this recorded the intent and never carried it
out, which meant the permanent log asserted surveillance that had not occurred.
A watch on a silent session now records nothing, because nothing was seen.

**Captured traffic never reaches the database.** Frames live in a bounded
in-memory deque and are dropped when the watch ends. The audit rows carry who
watched whom, for how long, and how many frames -- never a line of content.
Input is relayed unredacted to the watching operator, which means it can carry
a password typed at a login prompt; that is a live view held in memory for
minutes, and it must not become a permanent row in a table that outlives it.

Cost when nobody is watching
----------------------------

The taps sit on the final outbound flush and on ``data_in``, which every
message passes through. Both callers guard on :data:`WATCHES` being empty
before calling in, so an unwatched server pays one dict truthiness test per
message and nothing else -- no allocation, no call, no formatting.

Threading
---------

The taps run on the IO owner. The drain runs on a web worker sweeping the SSE
feed. They share a ``deque`` with a ``maxlen``, whose ``append`` and
``popleft`` are single bytecode operations and therefore atomic under the GIL,
so neither side takes a lock. Starting and stopping a watch is rare and does
take one.

"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from uuid import uuid4

from django.conf import settings

#: Seconds a watch runs before it expires on its own.
#:
#: A watch that never expires is one somebody forgets, and a forgotten watch
#: reads in the audit log as a single deliberate act when it was really days of
#: surveillance. Re-arming is one click and writes its own row, so a long watch
#: is recorded as the series of decisions it actually was.
DEFAULT_SECONDS = 1800

#: Frames one watch holds before the oldest are dropped.
DEFAULT_FRAMES = 500

#: Watches that may run at once, across all operators.
DEFAULT_LIMIT = 8

#: Characters of one captured line kept. Long enough for a paragraph of room
#: description, short enough that a pathological payload cannot fill the buffer
#: with one frame.
MAX_LINE = 2000

#: Watched sessid -> the watches on it. Read on the hot path without a lock;
#: mutated only under :data:`_LOCK`.
WATCHES: dict[int, list] = {}

_LOCK = threading.Lock()


def _setting(name, default):
    """Return one console watch setting, falling back to the default."""

    try:
        return int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        return default


@dataclass
class Watch:
    """One operator watching one session.

    Attributes:
        watch_id: Unique identifier for this one bounded watch lifetime.
        sessid: The watched session.
        watcher_id: Account primary key of the operator watching.
        watcher_name: Display name, for audit text only.
        account_name: The watched account, captured at start so the audit row
            can name it even if the session is gone by the time it is written.
        account_id: Primary key of the watched account, or ``None``.
        reason: Why the operator said they were watching.
        started: Wall-clock start, for the audit row.
        expires: Monotonic deadline.
        frames: Captured traffic, oldest first.
        delivered: Frames handed to the operator so far.
        began_recorded: Whether the permanent "began" row has been written.
        expiry_handle: Server-clock callback that enforces the deadline even
            when no console browser remains connected.
    """

    watch_id: str
    sessid: int
    watcher_id: int
    watcher_name: str
    account_name: str
    account_id: int | None
    reason: str
    started: float
    expires: float
    frames: deque = field(default_factory=deque)
    delivered: int = 0
    began_recorded: bool = False
    expiry_handle: object | None = field(default=None, repr=False, compare=False)


def _render(kwargs, *, strip_markup=False):
    """Flatten one outbound or inbound message to a readable line.

    Session traffic arrives as ``{cmdname: (args, kwargs)}``. An operator wants
    the text a person would have seen, not the envelope, so this takes the
    first positional argument of each command and labels anything else by name
    only -- a ``patch`` carrying a JSON document is noise on a watch feed, but
    knowing one went past is not.

    Args:
        kwargs: The normalized outbound frame or inbound message.
        strip_markup: Remove Evennia color markup from outbound text so the
            console transcript is readable without emulating a terminal.

    Returns:
        str: One line, or empty when there was nothing worth showing.
    """

    parts = []
    for cmdname, payload in (kwargs or {}).items():
        args = ()
        if isinstance(payload, (tuple, list)) and payload:
            args = payload[0] if isinstance(payload[0], (tuple, list)) else (payload[0],)
        first = args[0] if args else ""
        if cmdname == "text":
            text = str(first)
        elif isinstance(first, str) and first:
            text = f"[{cmdname}] {first}"
        else:
            text = f"[{cmdname}]"
        if text.strip():
            parts.append(text.strip())
    rendered = " ".join(parts)
    if strip_markup:
        from evennia.utils.ansi import strip_ansi

        rendered = strip_ansi(rendered)
    return rendered[:MAX_LINE]


def tap(session, direction, kwargs):
    """Record one message against every watch on this session.

    Called from the session handler's two funnels. Never raises: a fault in a
    watch must not be able to stop a player's traffic, which is the whole
    reason this is a tap rather than a filter.

    Args:
        session: The session the message belongs to.
        direction: ``"out"`` for server-to-player, ``"in"`` for the reverse.
        kwargs: The message.
    """

    try:
        watches = WATCHES.get(getattr(session, "sessid", None))
        if not watches:
            return
        line = _render(kwargs, strip_markup=direction == "out")
        if not line:
            return
        frame = {"at": time.time(), "dir": direction, "line": line}
        for entry in watches:
            entry.frames.append(frame)
    except Exception:  # noqa: BLE001
        # A watch is an observer. It does not get to break what it observes.
        pass


def start(sessid, session, watcher_id, watcher_name, reason):
    """Begin watching one session.

    No audit row is written here. The permanent record is written when the
    first frame is actually delivered, so a watch that sees nothing claims
    nothing.

    Args:
        sessid: The session to watch.
        session: The live session object, for the account it belongs to.
        watcher_id: Account primary key of the operator.
        watcher_name: Display name of the operator.
        reason: Why, recorded with the audit rows.

    Returns:
        Watch: The started watch.

    Raises:
        ValueError: This operator is already watching this session, or the
            concurrent watch limit is reached.
    """

    sweep()
    account = getattr(session, "account", None)
    now = time.monotonic()
    seconds = _setting("CONSOLE_WATCH_SECONDS", DEFAULT_SECONDS)
    entry = Watch(
        watch_id=uuid4().hex,
        sessid=int(sessid),
        watcher_id=int(watcher_id),
        watcher_name=str(watcher_name),
        account_name=str(getattr(account, "username", "") or ""),
        account_id=getattr(account, "pk", None),
        reason=str(reason)[:500],
        started=time.time(),
        expires=now + seconds,
        frames=deque(maxlen=_setting("CONSOLE_WATCH_FRAMES", DEFAULT_FRAMES)),
    )
    with _LOCK:
        existing = WATCHES.get(int(sessid), [])
        if any(other.watcher_id == int(watcher_id) for other in existing):
            raise ValueError("you are already watching this session")
        if sum(len(group) for group in WATCHES.values()) >= _setting(
            "CONSOLE_WATCH_LIMIT", DEFAULT_LIMIT
        ):
            raise ValueError("too many watches are running; stop one first")
        WATCHES[int(sessid)] = existing + [entry]
    try:
        from evennia.utils import clock

        entry.expiry_handle = clock.call_later(seconds, _expire, entry.watch_id)
    except Exception:
        with _LOCK:
            group = WATCHES.get(entry.sessid, [])
            remaining = [other for other in group if other is not entry]
            if remaining:
                WATCHES[entry.sessid] = remaining
            else:
                WATCHES.pop(entry.sessid, None)
        raise
    return entry


def _cancel_expiry(entry):
    """Cancel one watch's deadline callback when it ends another way."""

    handle = entry.expiry_handle
    entry.expiry_handle = None
    if handle is not None:
        handle.cancel()


def _expire(watch_id):
    """End exactly one watch lifetime when its server-clock deadline fires."""

    ended = None
    with _LOCK:
        for sessid, group in list(WATCHES.items()):
            matches = [entry for entry in group if entry.watch_id == str(watch_id)]
            if not matches:
                continue
            ended = matches[0]
            remaining = [entry for entry in group if entry is not ended]
            if remaining:
                WATCHES[sessid] = remaining
            else:
                WATCHES.pop(sessid, None)
            break
    if ended is None:
        return None
    ended.expiry_handle = None
    _record_end(ended, "the watch expired")
    return ended


def stop(sessid, watcher_id, why="stopped"):
    """End one operator's watch on one session.

    Args:
        sessid: The watched session.
        watcher_id: Account primary key of the operator.
        why: What ended it, for the audit row.

    Returns:
        Watch: The ended watch, or ``None`` if there was none.
    """

    with _LOCK:
        group = WATCHES.get(int(sessid), [])
        ended = [entry for entry in group if entry.watcher_id == int(watcher_id)]
        remaining = [entry for entry in group if entry.watcher_id != int(watcher_id)]
        if remaining:
            WATCHES[int(sessid)] = remaining
        else:
            WATCHES.pop(int(sessid), None)
    if not ended:
        return None
    _cancel_expiry(ended[0])
    _record_end(ended[0], why)
    return ended[0]


def stop_session(sessid, why="the session disconnected"):
    """End every watch on one session, because the session is gone."""

    with _LOCK:
        group = WATCHES.pop(int(sessid), [])
    for entry in group:
        _cancel_expiry(entry)
        _record_end(entry, why)
    return group


def sweep(now=None):
    """End every watch whose deadline has passed.

    Returns:
        list[Watch]: The watches that expired.
    """

    now = now if now is not None else time.monotonic()
    expired = []
    with _LOCK:
        for sessid in list(WATCHES):
            group = WATCHES.get(sessid, [])
            live = [entry for entry in group if entry.expires > now]
            expired.extend(entry for entry in group if entry.expires <= now)
            if live:
                WATCHES[sessid] = live
            else:
                WATCHES.pop(sessid, None)
    for entry in expired:
        _cancel_expiry(entry)
        _record_end(entry, "the watch expired")
    return expired


def drain(watcher_id, limit=200):
    """Return the frames captured for one operator since the last call.

    Writes the permanent "began" audit row the first time a watch actually
    delivers something, which is the point at which surveillance stopped being
    an intention and became a fact.

    Args:
        watcher_id: Account primary key of the operator.
        limit: Frames returned per watch per call.

    Returns:
        list[dict]: Payloads, each naming the session it came from.
    """

    sweep()
    out = []
    for sessid, group in list(WATCHES.items()):
        for entry in group:
            if entry.watcher_id != int(watcher_id):
                continue
            taken = []
            while entry.frames and len(taken) < limit:
                try:
                    taken.append(entry.frames.popleft())
                except IndexError:
                    break
            if not taken:
                continue
            if not entry.began_recorded:
                entry.began_recorded = True
                _record_began(entry)
            entry.delivered += len(taken)
            out.extend(
                {
                    "watch_id": entry.watch_id,
                    "sessid": sessid,
                    "account": entry.account_name,
                    "at": frame["at"],
                    "dir": frame["dir"],
                    "line": frame["line"],
                }
                for frame in taken
            )
    return out


def active(watcher_id=None, viewer_id=None):
    """Return running watches with ownership relative to one viewer.

    Args:
        watcher_id: When given, return only watches owned by this operator.
        viewer_id: Operator reading the list. Rows they own are marked
            ``mine`` without exposing account primary keys to the client.

    Returns:
        list[dict]: Active watch status rows.
    """

    now = time.monotonic()
    rows = []
    for sessid, group in list(WATCHES.items()):
        for entry in group:
            if watcher_id is not None and entry.watcher_id != int(watcher_id):
                continue
            rows.append(
                {
                    "watch_id": entry.watch_id,
                    "sessid": sessid,
                    "account": entry.account_name,
                    "watcher": entry.watcher_name,
                    "mine": viewer_id is not None and entry.watcher_id == int(viewer_id),
                    "reason": entry.reason,
                    "delivered": entry.delivered,
                    "pending": len(entry.frames),
                    "seconds_left": max(0, int(entry.expires - now)),
                }
            )
    return rows


def _target_ref(entry):
    """Return the audit target for one watch."""

    return f"accounts.accountdb#{entry.account_id if entry.account_id else ''}"


def _record_began(entry):
    """Record that a watch delivered its first frame.

    Permanent, because this is the row that lets other operators find out that
    a player was watched. It names the watcher, the watched, and the stated
    reason. It carries no captured content.
    """

    from evennia.console import audit

    audit.record(
        panel="sessions",
        operation="watch.began",
        actor_id=entry.watcher_id,
        actor_name=entry.watcher_name,
        target_ref=_target_ref(entry),
        after={
            "watch_id": entry.watch_id,
            "sessid": entry.sessid,
            "watched_account": entry.account_name,
            "watched_account_id": entry.account_id,
        },
        message=entry.reason,
        retention="permanent",
    )


def _record_end(entry, why):
    """Record that a watch ended, and how much it saw.

    Skipped entirely when nothing was ever delivered: a watch that saw nothing
    produced no "began" row, and an "ended" row without one would describe
    surveillance that did not happen.
    """

    if not entry.began_recorded:
        return
    from evennia.console import audit

    audit.record(
        panel="sessions",
        operation="watch.ended",
        actor_id=entry.watcher_id,
        actor_name=entry.watcher_name,
        target_ref=_target_ref(entry),
        after={
            "watch_id": entry.watch_id,
            "sessid": entry.sessid,
            "watched_account": entry.account_name,
            "watched_account_id": entry.account_id,
            "frames": entry.delivered,
            "seconds": int(time.time() - entry.started),
            "ended_because": why,
        },
        message=entry.reason,
        retention="permanent",
    )
