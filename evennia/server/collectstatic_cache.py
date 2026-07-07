"""Fingerprint cache to skip redundant ``collectstatic`` on reload."""

from __future__ import annotations

import hashlib
import os

from django.conf import settings


def _fingerprint_path(gamedir: str) -> str:
    return os.path.join(gamedir, "server", ".collectstatic_fingerprint")


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
    """Hash static source paths and file mtimes."""
    hasher = hashlib.sha256()
    for root in sorted(_iter_static_sources()):
        hasher.update(root.encode("utf-8"))
        for dirpath, _, filenames in os.walk(root):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                hasher.update(full.encode("utf-8"))
                hasher.update(str(int(st.st_mtime_ns)).encode("ascii"))
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fil:
        fil.write(fingerprint)


def static_sources_changed(gamedir: str) -> bool:
    current = compute_static_fingerprint()
    return current != read_cached_fingerprint(gamedir)
