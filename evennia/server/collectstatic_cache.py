"""Fingerprint cache to skip redundant ``collectstatic`` on reload."""

from __future__ import annotations

import hashlib
import os
import tempfile

from django.conf import settings


def _fingerprint_path(gamedir: str) -> str:
    return os.path.join(gamedir, "server", ".collectstatic_fingerprint")


def _static_root_signal() -> str:
    """Cheap fingerprint of the collect *destination* (existence + top-level count).

    Source-only fingerprinting can't tell that STATIC_ROOT was wiped out of band
    (container rebuild dropping the volume, ``rm -rf``); folding in whether it
    exists and roughly how many entries it holds makes a wipe bust the cache.
    """
    root = getattr(settings, "STATIC_ROOT", None)
    if not root or not os.path.isdir(root):
        return "static_root:missing"
    try:
        count = sum(1 for _ in os.scandir(root))
    except OSError:
        return "static_root:unreadable"
    return "static_root:%d" % count


def _iter_static_sources():
    dirs = getattr(settings, "STATICFILES_DIRS", None) or []
    for entry in dirs:
        if isinstance(entry, (tuple, list)):
            path = entry[0]
        else:
            path = entry
        if path and os.path.isdir(path):
            yield os.path.abspath(path)


def compute_static_fingerprint() -> str:
    """Hash static source paths, file sizes+mtimes, and a STATIC_ROOT signal.

    Size is included alongside mtime so a content change that doesn't advance
    mtime still busts the cache. Every field is NUL-delimited so path/size/mtime
    bytes can't run together and alias across entries.
    """
    hasher = hashlib.sha256()
    for root in sorted(_iter_static_sources()):
        hasher.update(root.encode("utf-8"))
        hasher.update(b"\0")
        for dirpath, _, filenames in os.walk(root):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                hasher.update(full.encode("utf-8"))
                hasher.update(b"\0")
                hasher.update(str(int(st.st_size)).encode("ascii"))
                hasher.update(b"\0")
                hasher.update(str(int(st.st_mtime_ns)).encode("ascii"))
                hasher.update(b"\0")
    hasher.update(_static_root_signal().encode("utf-8"))
    return hasher.hexdigest()


def read_cached_fingerprint(gamedir: str) -> str | None:
    path = _fingerprint_path(gamedir)
    try:
        with open(path, encoding="utf-8") as fil:
            return fil.read().strip() or None
    except OSError:
        return None


def write_cached_fingerprint(gamedir: str, fingerprint: str) -> None:
    path = _fingerprint_path(gamedir)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    # Write to a temp file in the same directory, then atomically replace, so a
    # crash mid-write can't leave a truncated fingerprint that thrashes the cache.
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".collectstatic_fingerprint.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fil:
            fil.write(fingerprint)
            fil.flush()
            os.fsync(fil.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def static_sources_changed(gamedir: str) -> bool:
    current = compute_static_fingerprint()
    return current != read_cached_fingerprint(gamedir)
