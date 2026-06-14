"""
Secure serialization for Portal <-> Server AMP traffic.

Every AMP payload uses a strict JSON envelope; pickle is never produced or
accepted on ANY AMP path (session, admin, status, or launcher control). A
non-JSON payload is rejected outright.

Wire formats:
  ``J1`` + UTF-8 JSON  — session messages (MsgPortal2Server / MsgServer2Portal)
  ``A1`` + UTF-8 JSON  — admin messages  (AdminPortal2Server / AdminServer2Portal)
  ``S1`` + UTF-8 JSON  — status reports  (MsgStatus, Portal -> launcher)
  ``L1`` + UTF-8 JSON  — launcher start args (MsgLauncher2Portal, launcher -> Portal)

Two distinct protections apply:

- Type-safety (no bytes, finite floats, JSON-safe ints, str keys) and pickle
  rejection run on *every* path, in both directions. This is the core hardening.
- Resource caps (string/list/dict/depth size limits) guard against *untrusted,
  player-originated* content arriving at the Server. The only such path is the
  Server unpacking ``MsgPortal2Server`` (a player's command text), so caps are
  enforced there and nowhere else. Server-generated output (``MsgServer2Portal``)
  and all admin control-plane traffic are trusted and legitimately large: the
  ``PSYNC``/``PCONNSYNC`` resync carries one ``sessiondata`` entry per connected
  session (scaling past any fixed key cap) in both directions. Capping those
  would reject valid traffic, so callers pass ``enforce_limits=False`` for them.
  Status and launcher-control payloads are likewise trusted (Portal- and
  launcher-originated) and pack with caps off.
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


def _max_depth() -> int:
    return int(getattr(settings, "AMP_SESSION_MAX_DEPTH", _MAX_DEPTH) or _MAX_DEPTH)


def sanitize_value(value: Any, *, depth: int = 0, enforce_limits: bool = True) -> Any:
    """
    Allow only JSON-safe primitives and containers.

    Type safety (no bytes, finite floats, JSON-safe int range, str dict keys) is
    always enforced. The resource caps (string/list/dict/depth/payload size) are a
    DoS guard against untrusted *inbound* payloads and are only enforced when
    ``enforce_limits`` is True. On the *pack* (send) path we serialize our own
    trusted data and AMP already frames/splits oversized payloads at the wire
    level, so callers there pass ``enforce_limits=False`` to avoid rejecting
    legitimate large outbound messages.
    """
    if enforce_limits and depth > _max_depth():
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
        if enforce_limits and len(value) > _MAX_STR_LEN:
            raise ValueError("AMP session string exceeds max length")
        return value
    if isinstance(value, (bytes, bytearray)):
        raise TypeError("bytes are not allowed in AMP session payloads")
    if isinstance(value, list):
        if enforce_limits and len(value) > _MAX_LIST_LEN:
            raise ValueError("AMP session list exceeds max length")
        return [sanitize_value(v, depth=depth + 1, enforce_limits=enforce_limits) for v in value]
    if isinstance(value, tuple):
        if enforce_limits and len(value) > _MAX_LIST_LEN:
            raise ValueError("AMP session tuple exceeds max length")
        return [sanitize_value(v, depth=depth + 1, enforce_limits=enforce_limits) for v in value]
    if isinstance(value, dict):
        if enforce_limits and len(value) > _MAX_DICT_KEYS:
            raise ValueError("AMP session dict exceeds max keys")
        out = {}
        for key, val in value.items():
            if not isinstance(key, str):
                raise TypeError("AMP session dict keys must be str")
            if enforce_limits and len(key) > 128:
                raise ValueError("AMP session dict key too long")
            out[key] = sanitize_value(val, depth=depth + 1, enforce_limits=enforce_limits)
        return out
    raise TypeError(f"unsupported AMP session type: {type(value).__name__}")


def sanitize_session_kwargs(kwargs: dict, *, enforce_limits: bool = True) -> dict:
    if not isinstance(kwargs, dict):
        raise TypeError("session kwargs must be a dict")
    return sanitize_value(kwargs, depth=0, enforce_limits=enforce_limits)


def pack_session_message(sessid: int, kwargs: dict) -> bytes:
    """Pack (sessid, kwargs) for Msg* AMP commands."""
    if not isinstance(sessid, int) or sessid < 0:
        raise ValueError("sessid must be a non-negative int")
    clean = sanitize_session_kwargs(kwargs, enforce_limits=False)
    body = json.dumps([sessid, clean], separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _SESSION_MAGIC + body


def unpack_session_message(data: bytes, *, enforce_limits: bool = True) -> Tuple[int, dict]:
    """Unpack session wire bytes to (sessid, kwargs).

    ``enforce_limits`` defaults True (the safe choice for untrusted player input
    arriving at the Server via ``MsgPortal2Server``). The Portal receiving
    server-generated output (``MsgServer2Portal``) passes False, since that data
    is trusted and may legitimately exceed the resource caps.
    """
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
        return sessid, sanitize_session_kwargs(kwargs, enforce_limits=enforce_limits)
    if raw[:1] in _PICKLE_REJECT_PREFIXES:
        raise ValueError("refusing non-JSON (pickle-like) AMP session payload")
    raise ValueError("unrecognized AMP session payload format")


_ADMIN_MAGIC = b"A1"


def _sanitize_admin_kwargs(kwargs: dict) -> dict:
    """
    Sanitize kwargs for an Admin* AMP message.

    Same rules as session kwargs, with one exception: the ``sessiondata``
    field uses integer sessid keys at the top level.  Those are converted to
    strings on the wire ("__si__:<N>") and restored on unpack.
    """
    if not isinstance(kwargs, dict):
        raise TypeError("admin kwargs must be a dict")
    result: dict = {}
    for k, v in kwargs.items():
        if not isinstance(k, str):
            raise TypeError("admin message kwargs keys must be str")
        if k == "sessiondata" and isinstance(v, dict):
            result[k] = _sanitize_admin_sessiondata(v)
        else:
            result[k] = sanitize_value(v, enforce_limits=False)
    return result


def _is_sessiondata_map(value: Any) -> bool:
    """Return True for ``{sessid: sessiondata}``, False for one sessiondata dict."""
    return isinstance(value, dict) and all(_is_int_key(key) for key in value)


def _is_int_key(key: Any) -> bool:
    return isinstance(key, int) or (isinstance(key, str) and key.isdigit())


def _sanitize_admin_sessiondata(
    value: dict, *, depth: int = 0, sessid_map: bool | None = None
) -> dict:
    # No resource caps here: this is trusted control-plane data, and the
    # sessid->sessiondata map holds one entry per connected session, which scales
    # past any fixed key/size cap. Type-safety is still enforced via sanitize_value.
    if sessid_map is None:
        sessid_map = _is_sessiondata_map(value)

    encoded: dict = {}
    for key, val in value.items():
        if sessid_map:
            if not _is_int_key(key):
                raise TypeError("AMP admin sessiondata map keys must be int-like")
            clean_key = f"__si__:{key}"
        elif isinstance(key, str):
            clean_key = key
        elif isinstance(key, int):
            clean_key = f"__ki__:{key}"
        else:
            raise TypeError("AMP admin sessiondata dict keys must be str or int")

        if isinstance(val, dict):
            encoded[clean_key] = _sanitize_admin_sessiondata(val, depth=depth + 1, sessid_map=False)
        else:
            encoded[clean_key] = sanitize_value(val, depth=depth + 1, enforce_limits=False)
    return encoded


def _restore_admin_sessiondata(value: dict) -> dict:
    restored: dict = {}
    for key, val in value.items():
        if key.startswith("__si__:"):
            clean_key = int(key[7:])
        elif key.startswith("__ki__:"):
            clean_key = int(key[7:])
        else:
            clean_key = key
        restored[clean_key] = _restore_admin_sessiondata(val) if isinstance(val, dict) else val
    return restored


def _restore_admin_kwargs(kwargs: dict) -> dict:
    """Reverse _sanitize_admin_kwargs — convert __si__:N keys back to int."""
    if "sessiondata" in kwargs and isinstance(kwargs["sessiondata"], dict):
        kwargs = dict(kwargs)
        kwargs["sessiondata"] = _restore_admin_sessiondata(kwargs["sessiondata"])
    return kwargs


def pack_admin_message(sessid: int, kwargs: dict) -> bytes:
    """Pack (sessid, kwargs) for Admin* AMP commands — JSON, no pickle."""
    if not isinstance(sessid, int) or sessid < 0:
        raise ValueError("admin sessid must be a non-negative int")
    clean = _sanitize_admin_kwargs(kwargs)
    body = json.dumps([sessid, clean], separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _ADMIN_MAGIC + body


def unpack_admin_message(data: bytes) -> Tuple[int, dict]:
    """Unpack Admin* wire bytes to (sessid, kwargs).

    No resource caps are applied: admin is trusted control-plane traffic and the
    ``PSYNC``/``PCONNSYNC`` resync legitimately carries one ``sessiondata`` entry
    per session in both directions. Pickle-like payloads are still rejected.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("packed_data must be bytes")
    raw = bytes(data)
    if raw.startswith(_ADMIN_MAGIC):
        parsed = json.loads(raw[2:].decode("utf-8"))
        if not isinstance(parsed, list) or len(parsed) != 2:
            raise ValueError("invalid AMP admin JSON envelope")
        sessid, kwargs = parsed[0], parsed[1]
        if not isinstance(sessid, int) or sessid < 0:
            raise ValueError("invalid sessid in AMP admin envelope")
        if not isinstance(kwargs, dict):
            raise ValueError("invalid kwargs in AMP admin envelope")
        return sessid, _restore_admin_kwargs(kwargs)
    if raw[:1] in _PICKLE_REJECT_PREFIXES:
        raise ValueError("refusing non-JSON (pickle-like) AMP admin payload")
    raise ValueError("unrecognized AMP admin payload format")


_STATUS_MAGIC = b"S1"
_LAUNCHER_MAGIC = b"L1"


def pack_status(status: Any) -> bytes:
    """Pack the Portal/Server status report for ``MsgStatus`` — JSON, no pickle.

    ``status`` is the 6-element tuple returned by ``get_status()``:
    ``(portal_live, server_live, portal_pid, server_pid, portal_info, server_info)``.
    It is trusted, Portal-generated control data, so resource caps are off.
    Tuples serialize as JSON arrays and are restored as lists; the consumer
    unpacks them positionally, so the distinction does not matter.
    """
    clean = sanitize_value(status, enforce_limits=False)
    body = json.dumps(clean, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _STATUS_MAGIC + body


def unpack_status(data: bytes) -> Any:
    """Unpack ``MsgStatus`` wire bytes to the status list. Pickle is rejected."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("status payload must be bytes")
    raw = bytes(data)
    if raw.startswith(_STATUS_MAGIC):
        return sanitize_value(json.loads(raw[2:].decode("utf-8")), enforce_limits=False)
    if raw[:1] in _PICKLE_REJECT_PREFIXES:
        raise ValueError("refusing non-JSON (pickle-like) AMP status payload")
    raise ValueError("unrecognized AMP status payload format")


def pack_launcher_args(arguments: Any) -> bytes:
    """Pack launcher start args for ``MsgLauncher2Portal`` — JSON, no pickle.

    ``arguments`` is the server twistd command line (a ``list`` of ``str``) for a
    (re)start, or an empty ``dict`` for control operations that carry no args. It
    is trusted, launcher-originated control data, so resource caps are off.
    """
    clean = sanitize_value(arguments, enforce_limits=False)
    body = json.dumps(clean, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _LAUNCHER_MAGIC + body


def unpack_launcher_args(data: bytes) -> Any:
    """Unpack ``MsgLauncher2Portal`` start-arg wire bytes. Pickle is rejected."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("launcher arguments must be bytes")
    raw = bytes(data)
    if raw.startswith(_LAUNCHER_MAGIC):
        return sanitize_value(json.loads(raw[2:].decode("utf-8")), enforce_limits=False)
    if raw[:1] in _PICKLE_REJECT_PREFIXES:
        raise ValueError("refusing non-JSON (pickle-like) AMP launcher payload")
    raise ValueError("unrecognized AMP launcher payload format")


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
