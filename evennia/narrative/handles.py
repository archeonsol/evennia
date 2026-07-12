"""
Viewer-scoped entity handles for interactive rich-client references.

A rich client needs a stable token to hang click/hover interactivity on and to
send back to the server ("target that person"). Emitting the raw database id
(``char_id``) leaks a stable identity that a modified client can correlate
across disguises: the sdesc a viewer *sees* changes, but the id does not.

An entity handle is an opaque, per-viewer token derived from **what the viewer
perceives** (the resolved name-as-seen), salted per viewer. Properties:

- No database id crosses the wire.
- Two disguised appearances of the same character to a viewer who cannot see
  through the disguise resolve to *different* handles (the perceived name
  differs) — they cannot be correlated.
- When the viewer legitimately recognizes the character (recog policy makes the
  perceived name canonical), the handle is stable across appearances — the
  reveal the recog policy already authorized, and nothing more.
- The server resolves a handle back to the concrete entity only for the viewer
  it was issued to (:func:`resolve_handle`), so interactivity still works.

The registry lives on ``viewer.ndb`` (process-local, dropped on reload) and is
bounded so a long scene cannot grow it without limit.
"""

from __future__ import annotations

import hashlib
import os
import time

# Cap the per-viewer handle map; oldest entries are evicted past this.
_MAX_HANDLES = 512
HANDLE_TTL_SECONDS = 15 * 60
_SALT_ATTR = "_entity_handle_salt"
_MAP_ATTR = "_entity_handles"


def _salt(viewer) -> bytes:
    """Per-viewer random salt so handles are not guessable or cross-viewer stable."""
    ndb = getattr(viewer, "ndb", None)
    if ndb is None:
        return b""
    salt = getattr(ndb, _SALT_ATTR, None)
    if salt is None:
        salt = os.urandom(16)
        setattr(ndb, _SALT_ATTR, salt)
    return salt


def _map(viewer) -> dict | None:
    ndb = getattr(viewer, "ndb", None)
    if ndb is None:
        return None
    m = getattr(ndb, _MAP_ATTR, None)
    if m is None:
        m = {}
        setattr(ndb, _MAP_ATTR, m)
    return m


def handle_for(viewer, char, perceived_name: str) -> str:
    """
    Issue (or reuse) the handle for ``char`` as ``viewer`` currently perceives it.

    Args:
        viewer: The recipient the handle is scoped to.
        char: The referenced entity (its id is stored server-side only).
        perceived_name: The name this viewer saw for ``char`` — the perception
            token the handle derives from.

    Returns:
        str: A short opaque handle, or ``""`` when a viewer cannot hold state.
    """
    salt = _salt(viewer)
    m = _map(viewer)
    if m is None:
        return ""
    cid = getattr(char, "id", None)
    # Include the server-only entity id in the keyed input so two indistinguishable
    # candidates remain independently targetable inside one scene. The salt keeps
    # that id unrecoverable and viewer-specific; the perception token rotates the
    # handle whenever the authorized presentation changes.
    digest = hashlib.blake2s(
        salt + str(cid).encode("ascii", "replace") + b"\0" + str(perceived_name).encode("utf-8"),
        digest_size=12,
    )
    handle = "e" + digest.hexdigest()
    now = time.monotonic()
    if handle not in m and len(m) >= _MAX_HANDLES:
        # bounded: drop the oldest inserted entry (dicts preserve insertion order)
        try:
            del m[next(iter(m))]
        except StopIteration:
            pass
    m[handle] = {"entity_id": cid, "issued_at": now, "last_used_at": now}
    return handle


def resolve_handle(viewer, handle: str):
    """
    Resolve a handle previously issued to ``viewer`` back to a concrete object.

    Returns:
        The referenced object, or ``None`` if the handle is unknown to this
        viewer (expired, never issued, or forged).
    """
    m = _map(viewer)
    if not m or handle not in m:
        return None
    entry = m[handle]
    if not isinstance(entry, dict):
        # Compatibility with handles issued by the first R1B prototype.
        entry = {"entity_id": entry, "issued_at": time.monotonic(), "last_used_at": 0.0}
    now = time.monotonic()
    if now - float(entry.get("issued_at") or 0.0) > HANDLE_TTL_SECONDS:
        m.pop(handle, None)
        return None
    entry["last_used_at"] = now
    cid = entry.get("entity_id")
    if cid is None:
        return None
    from evennia.objects.models import ObjectDB

    return ObjectDB.objects.filter(pk=cid).first()


def handles_for_entity(viewer, entity) -> tuple[str, ...]:
    """Return currently issued handles for ``entity`` in this viewer context."""
    m = _map(viewer)
    if not m:
        return ()
    entity_id = getattr(entity, "id", entity)
    now = time.monotonic()
    found = []
    expired = []
    for handle, entry in tuple(m.items()):
        if not isinstance(entry, dict):
            entry = {"entity_id": entry, "issued_at": now}
        if now - float(entry.get("issued_at") or 0.0) > HANDLE_TTL_SECONDS:
            expired.append(handle)
        elif entry.get("entity_id") == entity_id:
            found.append(handle)
    for handle in expired:
        m.pop(handle, None)
    return tuple(found)
