"""The error inbox.

``core-beliefs.md`` says the framework should avoid requiring external services
for core functionality. Error tracking is core functionality for anyone running
a game, and until now the answer was `grep`.

Tracebacks are grouped by signature, so a fault that fires four thousand times
is one row with a count rather than four thousand lines to scroll past. The
signature is the exception type plus the call path as ``(file, function)``
pairs -- deliberately *without* line numbers, so an unrelated edit above a
fault does not split its history in two, while a genuine change to the call
path does.

Nothing new is captured. The panel reads the same log files the tail reads, so
there is no second capture path, no new retention policy, and no table that
grows on its own. Only an operator's judgement is persisted: which signatures
have been acknowledged or muted, and why.

"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from evennia.console.models import ConsoleErrorState
from evennia.console.registry import Panel, io_action

#: Log line prefix Evennia writes: a timestamp and a bracketed level marker.
LINE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[[^\]]{1,4}\]\s?(.*)$")

#: The line every Python traceback opens with.
TRACEBACK_START = "Traceback (most recent call last):"

#: A traceback frame's location line.
FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>.+)$')

#: The closing line: ``ExceptionType: message``, or a bare type.
EXCEPTION = re.compile(
    r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt))\b:?(?P<message>.*)$"
)

#: Bytes read from the end of each log file. A log is unbounded; a page is not.
SCAN_BYTES = 2 * 1024 * 1024

#: Signatures returned in one response.
MAX_GROUPS = 200


@dataclass
class Occurrence:
    """One observed traceback."""

    exception: str
    message: str
    frames: tuple
    when: str = ""
    source: str = ""
    raw: list = field(default_factory=list)

    def signature(self) -> str:
        """Return the stable identity of this fault.

        Line numbers are excluded on purpose: an edit somewhere above a fault
        shifts every line below it, and a signature built on line numbers would
        report the same bug as new every time the file was touched.
        """

        parts = [self.exception] + [f"{path}:{func}" for path, _, func in self.frames]
        return hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=16).hexdigest()


def strip_prefix(line):
    """Split one log line into ``(timestamp, body)``.

    Returns:
        tuple: The timestamp and the message, or ``("", line)`` when the line
        carries no prefix.
    """

    match = LINE_PREFIX.match(line)
    if match:
        return match.group(1), match.group(2)
    return "", line


def parse_tracebacks(lines, source=""):
    """Return every traceback found in a sequence of log lines.

    Args:
        lines: Raw log lines, prefixes included.
        source: Label of the file they came from.

    Returns:
        list[Occurrence]: One entry per traceback, in the order found.
    """

    found = []
    current = None
    for raw in lines:
        when, body = strip_prefix(raw)

        if body.strip() == TRACEBACK_START:
            current = {"when": when, "frames": [], "raw": [body]}
            continue
        if current is None:
            continue

        current["raw"].append(body)
        frame = FRAME.match(body)
        if frame:
            current["frames"].append(
                (frame.group("file"), int(frame.group("line")), frame.group("func").strip())
            )
            continue
        if body.startswith(" ") or not body.strip():
            # Source echo beneath a frame, or a blank line inside the block.
            continue

        match = EXCEPTION.match(body.strip())
        if match:
            found.append(
                Occurrence(
                    exception=match.group("type"),
                    message=match.group("message").strip()[:400],
                    frames=tuple(current["frames"]),
                    when=current["when"],
                    source=source,
                    raw=current["raw"][-40:],
                )
            )
        current = None
    return found


def _log_files():
    """Return the configured log files that exist, by label."""

    found = {}
    for label, setting in (
        ("server", "SERVER_LOG_FILE"),
        ("portal", "PORTAL_LOG_FILE"),
        ("lockwarning", "LOCKWARNING_LOG_FILE"),
    ):
        path = getattr(settings, setting, "")
        if path and os.path.exists(path):
            found[label] = path
    return found


def _tail_lines(path):
    """Return the lines in the last :data:`SCAN_BYTES` of a file."""

    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if size > SCAN_BYTES:
                handle.seek(size - SCAN_BYTES)
                handle.readline()
            return handle.read().splitlines()
    except OSError:
        return []


class ErrorsPanel(Panel):
    """Tracebacks, grouped by fault rather than listed by occurrence."""

    key = "errors"
    label = "Errors"
    description = "Tracebacks grouped by signature, with counts and first sighting."
    columns = ("exception", "count", "last_seen")
    needs_io = False

    def rows(self, ctx):
        """Return grouped faults from the recent log window.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``state`` to filter
                by review state, and ``search``.

        Returns:
            dict: Groups newest-first, plus the window they came from.
        """

        params = ctx.params
        wanted_state = str(params.get("state") or "").strip().lower()
        search = str(params.get("search") or "").strip().lower()

        occurrences = []
        files = _log_files()
        for label, path in files.items():
            occurrences.extend(parse_tracebacks(_tail_lines(path), source=label))

        groups = {}
        for item in occurrences:
            signature = item.signature()
            group = groups.get(signature)
            if group is None:
                groups[signature] = {
                    "signature": signature,
                    "exception": item.exception,
                    "message": item.message,
                    "source": item.source,
                    "frames": [
                        {"file": path, "line": line, "function": func}
                        for path, line, func in item.frames
                    ],
                    "count": 1,
                    "first_seen": item.when,
                    "last_seen": item.when,
                    "raw": item.raw,
                }
                continue
            group["count"] += 1
            group["last_seen"] = item.when or group["last_seen"]
            if item.when and item.when < group["first_seen"]:
                group["first_seen"] = item.when

        states = {
            row["signature"]: row
            for row in ConsoleErrorState.objects.filter(signature__in=list(groups)).values(
                "signature", "state", "note", "actor_name", "updated_at"
            )
        }

        rows = []
        for group in groups.values():
            record = states.get(group["signature"])
            group["state"] = record["state"] if record else ConsoleErrorState.STATE_OPEN
            group["note"] = record["note"] if record else ""
            group["reviewed_by"] = record["actor_name"] if record else ""
            if wanted_state and group["state"] != wanted_state:
                continue
            if search and search not in (group["exception"] + group["message"]).lower():
                continue
            rows.append(group)

        rows.sort(key=lambda row: (row["last_seen"], row["count"]), reverse=True)
        return {
            "rows": rows[:MAX_GROUPS],
            "group_count": len(rows),
            "occurrence_count": len(occurrences),
            "files": sorted(files),
            "window_bytes": SCAN_BYTES,
            "states": [choice[0] for choice in ConsoleErrorState.STATE_CHOICES],
            "note": (
                "Grouped from the recent end of each log file. Signatures ignore line "
                "numbers, so an edit above a fault does not split its history."
            ),
        }

    @io_action
    def review(self, ctx, signature=None, state=None, note=""):
        """Record a judgement about one fault.

        Args:
            ctx: IO context.
            signature: The fault's signature.
            state: One of the review states.
            note: Why, for whoever reads this next.

        Returns:
            dict: The stored judgement.

        Raises:
            LookupError: The signature or state is missing or unknown.
        """

        signature = str(signature or "").strip()
        if not signature:
            raise LookupError("a signature is required")
        wanted = str(state or "").strip().lower()
        if wanted not in {choice[0] for choice in ConsoleErrorState.STATE_CHOICES}:
            raise LookupError(f"{state!r} is not a review state")

        row, _ = ConsoleErrorState.objects.update_or_create(
            signature=signature,
            defaults={
                "state": wanted,
                "note": str(note or "")[:500],
                "actor_id": ctx.actor_id,
                "actor_name": str(ctx.actor_name or "")[:255],
                "updated_at": timezone.now(),
            },
        )
        return {
            "signature": row.signature,
            "state": row.state,
            "note": row.note,
            "reviewed_by": row.actor_name,
        }
