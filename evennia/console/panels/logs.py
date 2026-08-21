"""Log files, live and historical.

The live tail arrives over the feed; this panel provides what the feed cannot:
which files exist, how large they are, what is already in them, and the rotated
backups sitting beside them. An operator opening the console mid-incident wants
the last hundred lines before they want the next one.

Reads plain files, so it works with the game server down -- which is when logs
are most worth reading.

"""

from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings

from evennia.console.registry import Panel

#: Log files this deployment writes, by the setting that names each.
LOG_SETTINGS = (
    ("server", "SERVER_LOG_FILE"),
    ("portal", "PORTAL_LOG_FILE"),
    ("http", "HTTP_LOG_FILE"),
    ("lockwarning", "LOCKWARNING_LOG_FILE"),
)

#: Lines one request may return.
MAX_LINES = 500

#: Bytes read from the end of a file to satisfy one request. A log is
#: unbounded; a page is not.
TAIL_BYTES = 512 * 1024


def log_files() -> dict:
    """Return the configured log files that exist, by label."""

    found = {}
    for label, setting in LOG_SETTINGS:
        path = getattr(settings, setting, "")
        if path and os.path.exists(path):
            found[label] = path
    return found


def _backups(path):
    """Return rotated backups beside one log file, newest first.

    Twisted's rotation appends a suffix rather than renaming into a directory,
    so the backups sit next to the live file.
    """

    target = Path(path)
    parent = target.parent
    if not parent.exists():
        return []
    rows = []
    for candidate in parent.glob(f"{target.name}.*"):
        try:
            stat = candidate.stat()
        except OSError:
            continue
        rows.append(
            {
                "name": candidate.name,
                "size_bytes": stat.st_size,
                "modified": int(stat.st_mtime),
            }
        )
    return sorted(rows, key=lambda row: -row["modified"])


class LogsPanel(Panel):
    """Read the tail of a log file, filter it, and list its backups."""

    key = "logs"
    label = "Logs"
    description = "The tail of each log file, with the live feed on top."
    columns = ("source", "line")
    needs_io = False

    def rows(self, ctx):
        """Return the tail of one log file.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``file``, ``lines``,
                ``search``, and ``level``.

        Returns:
            dict: The lines, the files available, and the rotation policy.

        Raises:
            LookupError: The named file is not one this deployment writes.
        """

        params = ctx.params
        files = log_files()
        if not files:
            return {
                "files": [],
                "file": "",
                "rows": [],
                "note": "This deployment writes no log files, or none exist yet.",
            }

        label = str(params.get("file") or "").strip().lower() or next(iter(files))
        if label not in files:
            raise LookupError(f"{label!r} is not a log file this deployment writes")
        path = files[label]

        try:
            wanted = int(params.get("lines") or 200)
        except (TypeError, ValueError):
            wanted = 200
        wanted = max(1, min(wanted, MAX_LINES))

        lines = self._tail(path, wanted)
        lines = self._filtered(lines, params.get("search"), params.get("level"))

        return {
            "file": label,
            "path": path,
            "files": [
                {"label": name, "size_bytes": self._size(target)}
                for name, target in sorted(files.items())
            ],
            "rows": [{"source": label, "line": line} for line in lines],
            "line_count": len(lines),
            "backups": _backups(path),
            "retention": {
                "days": getattr(settings, "LOG_ROTATED_RETENTION_DAYS", None),
                "max_backups": getattr(settings, "LOG_ROTATED_MAX_BACKUPS", None),
            },
            "live_topics": ["log"],
        }

    def _size(self, path):
        """Return one file's size, or zero when it cannot be read."""

        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _tail(self, path, wanted):
        """Return the last ``wanted`` lines of a file.

        Seeks rather than reads the whole file: a server log runs to hundreds
        of megabytes, and a page wants the end of it.
        """

        try:
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                if size > TAIL_BYTES:
                    handle.seek(size - TAIL_BYTES)
                    handle.readline()  # discard the partial line seeking landed in
                text = handle.read()
        except OSError:
            return []
        return [line for line in text.splitlines() if line.strip()][-wanted:]

    def _filtered(self, lines, search, level):
        """Apply the free-text and level filters, server side.

        Filtered here rather than in the browser so a noisy file does not have
        to cross the wire before being discarded.
        """

        term = str(search or "").strip()
        if term:
            try:
                pattern = re.compile(term, re.IGNORECASE)
            except re.error:
                # An operator half-way through typing a regex should see no
                # matches, not an error page.
                return []
            lines = [line for line in lines if pattern.search(line)]

        wanted_level = str(level or "").strip().upper()
        if wanted_level:
            lines = [line for line in lines if wanted_level in line.upper()]
        return lines
