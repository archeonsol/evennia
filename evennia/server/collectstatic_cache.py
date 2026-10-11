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


def _app_static_dirs():
    """The ``static`` folder of every installed app, as collectstatic's app finder reads them.

    The engine's own web client (``evennia/web/static``) is one of them. It is not in
    ``STATICFILES_DIRS``, so a fingerprint of that alone never saw a release that changed
    only the client: the new bundle was not collected and the old one kept being served.
    """
    try:
        from django.contrib.staticfiles.finders import AppDirectoriesFinder

        return [
            storage.location
            for storage in AppDirectoriesFinder().storages.values()
            if getattr(storage, "location", None)
        ]
    except Exception:  # noqa: BLE001 - a fingerprint must never stop a start
        return []


def _iter_static_sources():
    """Every folder collectstatic reads: ``STATICFILES_DIRS`` and each app's ``static``."""
    seen = set()
    entries = list(getattr(settings, "STATICFILES_DIRS", None) or [])
    for entry in entries + _app_static_dirs():
        # A (prefix, path) pair names the folder by its path, the last item.
        path = entry[-1] if isinstance(entry, (tuple, list)) else entry
        if not path or not os.path.isdir(path):
            continue
        full = os.path.abspath(path)
        if full not in seen:
            seen.add(full)
            yield full


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
