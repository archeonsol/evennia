"""
Moderation substrate.

Connection history, staff-issued sanctions, and the flag queue that automated
detection writes into. Models live in :mod:`evennia.server.models` alongside
``GameEvent`` and the authorization tables, following the same convention: the
substrate references accounts by id and name rather than by foreign key, so
moderation history outlives the account it describes.

Two rules hold across everything here.

**Hard signals only.** What a connection reports about itself is recorded;
nothing is inferred, scored, or stylometrically guessed. Accounts are linked by
exact matches on recorded key columns, so any conclusion can be shown to the
player it is used against.

**No automatic sanctions.** Detection writes ``ModerationFlag`` rows. Only a
staff action turns a flag into a ``Sanction``. Games supply the policy -- which
observations are worth flagging, and at what severity -- and the staff surface
that reviews them.

Nothing is imported eagerly: ``evennia.server.models`` is loaded while the app
registry is still populating, and pulling the engine logger in at that point
would invert the import order.
"""

from __future__ import annotations

_CAPTURE = {
    "client_fingerprint",
    "derive_cidr",
    "hash_value",
    "normalize_address",
    "record_session",
    "snapshot_session",
}
_SANCTIONS = {
    "Decision",
    "SanctionError",
    "evaluate",
    "issue_sanction",
    "keys_from_snapshot",
    "match_sanctions",
    "normalize_subject",
    "record_hits",
    "revoke_sanction",
    "verify_chain",
}

__all__ = sorted(_CAPTURE | _SANCTIONS)


def __getattr__(name):
    if name in _CAPTURE:
        from evennia.moderation import capture

        return getattr(capture, name)
    if name in _SANCTIONS:
        from evennia.moderation import sanctions

        return getattr(sanctions, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
