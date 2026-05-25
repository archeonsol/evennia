"""
Secure serialization for Portal <-> Server session traffic (Msg* AMP commands).

Hot-path session I/O uses a strict JSON envelope. Pickle is reserved for Admin*
sync payloads only (see ``evennia.server.portal.amp.dumps`` / ``loads``).

Wire format: ``J1`` + UTF-8 JSON ``[sessid, kwargs]`` with recursively sanitized values.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple, Union

from django.conf import settings

# Pickle protocol 2+ opcodes start with 0x80; reject on session path.
_PICKLE_REJECT_PREFIXES = (b"\x80", b"(", b"]", b"}")

_SESSION_MAGIC = b"J1"

_SUBJECT_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")

_MAX_DEPTH = 12
_MAX_STR_LEN = 65536
_MAX_LIST_LEN = 256
_MAX_DICT_KEYS = 128
_MAX_PAYLOAD_BYTES = 512 * 1024


def session_serde_enabled() -> bool:
    return str(getattr(settings, "AMP_SESSION_SERDE", "json")).lower() != "pickle"


def accept_legacy_session_pickle() -> bool:
    return bool(getattr(settings, "AMP_SESSION_ACCEPT_LEGACY_PICKLE", False))


def _max_depth() -> int:
    return int(getattr(settings, "AMP_SESSION_MAX_DEPTH", _MAX_DEPTH) or _MAX_DEPTH)


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    """
    Allow only JSON-safe primitives and containers with bounded size/depth.
    """
    if depth > _max_depth():
        raise ValueError("AMP session payload exceeds max nesting depth")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value < -(2**53 - 1) or value > (2**53 - 1):
            raise ValueError("AMP session integer out of safe JSON range")
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # noqa: PLR0124
            raise ValueError("AMP session float must be finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STR_LEN:
            raise ValueError("AMP session string exceeds max length")
        return value
    if isinstance(value, (bytes, bytearray)):
        raise TypeError("bytes are not allowed in AMP session payloads")
    if isinstance(value, list):
        if len(value) > _MAX_LIST_LEN:
            raise ValueError("AMP session list exceeds max length")
        return [sanitize_value(v, depth=depth + 1) for v in value]
    if isinstance(value, tuple):
        if len(value) > _MAX_LIST_LEN:
            raise ValueError("AMP session tuple exceeds max length")
        return [sanitize_value(v, depth=depth + 1) for v in value]
    if isinstance(value, dict):
        if len(value) > _MAX_DICT_KEYS:
            raise ValueError("AMP session dict exceeds max keys")
        out = {}
        for key, val in value.items():
            if not isinstance(key, str):
                raise TypeError("AMP session dict keys must be str")
            if len(key) > 128:
                raise ValueError("AMP session dict key too long")
            out[key] = sanitize_value(val, depth=depth + 1)
        return out
    raise TypeError(f"unsupported AMP session type: {type(value).__name__}")


def sanitize_session_kwargs(kwargs: dict) -> dict:
    if not isinstance(kwargs, dict):
        raise TypeError("session kwargs must be a dict")
    return sanitize_value(kwargs, depth=0)


def pack_session_message(sessid: int, kwargs: dict) -> bytes:
    """Pack (sessid, kwargs) for Msg* AMP commands."""
    if not isinstance(sessid, int) or sessid < 0:
        raise ValueError("sessid must be a non-negative int")
    clean = sanitize_session_kwargs(kwargs)
    body = json.dumps([sessid, clean], separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > _MAX_PAYLOAD_BYTES:
        raise ValueError("AMP session payload exceeds max size")
    return _SESSION_MAGIC + body


def unpack_session_message(data: bytes) -> Tuple[int, dict]:
    """Unpack session wire bytes to (sessid, kwargs)."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("packed_data must be bytes")
    raw = bytes(data)
    if raw.startswith(_SESSION_MAGIC):
        parsed = json.loads(raw[2:].decode("utf-8"))
        if not isinstance(parsed, list) or len(parsed) != 2:
            raise ValueError("invalid AMP session JSON envelope")
        sessid, kwargs = parsed[0], parsed[1]
        if not isinstance(sessid, int) or sessid < 0:
            raise ValueError("invalid sessid in AMP session envelope")
        if not isinstance(kwargs, dict):
            raise ValueError("invalid kwargs in AMP session envelope")
        return sessid, sanitize_session_kwargs(kwargs)
    if raw[:1] in _PICKLE_REJECT_PREFIXES:
        if accept_legacy_session_pickle():
            from evennia.server.portal import amp

            msg = amp.loads(raw)
            if not isinstance(msg, (list, tuple)) or len(msg) != 2:
                raise ValueError("legacy pickle session message malformed")
            return int(msg[0]), sanitize_session_kwargs(msg[1])
        raise ValueError(
            "refusing legacy pickle AMP session payload (enable AMP_SESSION_ACCEPT_LEGACY_PICKLE only for migration)"
        )
    raise ValueError("unrecognized AMP session payload format")


def validate_event_subject(subject: str) -> str:
    subject = (subject or "").strip().lower()
    if not _SUBJECT_RE.match(subject):
        raise ValueError(f"invalid event subject: {subject!r}")
    return subject


def sanitize_event_payload(payload: Any) -> Dict[str, Any]:
    """Event/job payloads must be a sanitized dict."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise TypeError("event payload must be a dict")
    return sanitize_session_kwargs(payload)
