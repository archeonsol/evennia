"""
Per-command trace context for structured logs (session, caller, command, trace id).
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any, Dict, Optional

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "evennia_command_trace_id", default=None
)
_trace_meta: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "evennia_command_trace_meta", default=None
)


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def get_trace_meta() -> Dict[str, Any]:
    return dict(_trace_meta.get() or {})


def trace_log_context() -> Dict[str, Any]:
    """Extra fields for logging filters / structlog ``bind_contextvars``."""
    meta = get_trace_meta()
    tid = get_trace_id()
    if tid:
        meta = {**meta, "trace_id": tid}
    return meta


def begin_command_trace(
    *,
    caller=None,
    session=None,
    raw_string: str = "",
    cmd_key: str = "",
) -> str:
    """
    Start a command-scoped trace. Returns the trace id (16 hex chars).
    """
    trace_id = uuid.uuid4().hex[:16]
    _trace_id.set(trace_id)
    meta = {
        "trace_id": trace_id,
        "raw_string": (raw_string or "")[:200],
        "cmd_key": cmd_key or "",
    }
    if session is not None:
        meta["session_id"] = getattr(session, "sessid", None)
        meta["session_key"] = getattr(session, "key", None) or getattr(session, "id", None)
    if caller is not None:
        meta["caller_key"] = getattr(caller, "key", None)
        meta["caller_id"] = getattr(caller, "id", None)
        meta["caller_type"] = caller.__class__.__name__
    _trace_meta.set(meta)
    return trace_id


def end_command_trace() -> None:
    _trace_id.set(None)
    _trace_meta.set(None)


def format_trace_prefix() -> str:
    """Short prefix for Evennia log lines when trace context is active."""
    tid = get_trace_id()
    if not tid:
        return ""
    meta = get_trace_meta()
    cmd = meta.get("cmd_key") or meta.get("raw_string") or ""
    if cmd:
        return "[trace:%s cmd:%s] " % (tid, str(cmd)[:40])
    return "[trace:%s] " % tid
