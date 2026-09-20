"""
Per-command instrumentation: trace context for structured logs (session,
caller, command, trace id), the outcome metric, and completion markers.
"""

from __future__ import annotations

import contextvars
import itertools
import time
from typing import Any, Dict, Optional

_TRACE_SEQUENCE = itertools.count(1)


def _next_trace_id() -> str:
    """Return the next process-local trace id.

    A monotonic counter replaces ``uuid4`` on the per-command path: the id only
    needs to be unique within this process for log correlation, and uuid4 was
    measurable allocation churn under load. The hex width matches the previous
    format (16 characters).
    """

    return f"{next(_TRACE_SEQUENCE):016x}"


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


def _safe_attr(obj, *names):
    """First non-None attribute value; a game-defined property that raises contributes nothing."""
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            continue
        if value is not None:
            return value
    return None


def begin_command_trace(
    *,
    caller=None,
    session=None,
    raw_string: str = "",
    cmd_key: str = "",
) -> str:
    """
    Start a command-scoped trace. Returns the trace id (16 hex chars).

    Identity fields are read defensively: a game-defined ``key``/``id``
    property must not turn trace instrumentation into a dispatch failure.
    """
    trace_id = _next_trace_id()
    _trace_id.set(trace_id)
    meta = {
        "trace_id": trace_id,
        "raw_string": (raw_string or "")[:200],
        "cmd_key": cmd_key or "",
    }
    if session is not None:
        meta["session_id"] = _safe_attr(session, "sessid")
        meta["session_key"] = _safe_attr(session, "key", "id")
    if caller is not None:
        meta["caller_key"] = _safe_attr(caller, "key")
        meta["caller_id"] = _safe_attr(caller, "id")
        meta["caller_type"] = caller.__class__.__name__
    _trace_meta.set(meta)
    return trace_id


def end_command_trace() -> None:
    _trace_id.set(None)
    _trace_meta.set(None)


class command_trace_scope:
    """One seam around a command dispatch for trace, outcome metric, markers.

    Use as a context manager; set ``.outcome`` inside the body (default and
    post-raise value is ``"error"``). Exit side effects run in protocol order:
    the outcome metric (when ``metrics=True``), the trace clear, then the
    ``EV-COMMAND-DONE`` marker (when ``markers=True`` and
    ``COMMAND_COMPLETION_MARKERS_ENABLED``). Marker-last is the protocol
    contract: the marker completes the one command in flight for this
    session. The engine does not serialize a session's input (inputfuncs
    fires each pipelined line on its own task) and the marker carries no
    command id, so a client that relies on markers must send one command and
    await its marker before sending the next. A per-command sequence id
    would make markers unambiguous under pipelining.

    Args:
        caller (object): The caller, for trace metadata.
        session (object): The input session, for trace metadata and the
            marker sink (a session without ``msg`` is skipped silently).
        raw_string (str): Full input line, truncated for trace metadata.
        cmd_key (str): Command keyword, for trace metadata.
        metrics (bool): Record ``record_action_input(outcome, elapsed)``.
        markers (bool): Emit the completion marker to ``session``.

    """

    def __init__(
        self,
        *,
        caller=None,
        session=None,
        raw_string: str = "",
        cmd_key: str = "",
        metrics: bool = False,
        markers: bool = False,
    ):
        self._caller = caller
        self._session = session
        self._raw_string = raw_string
        self._cmd_key = cmd_key
        self._metrics = metrics
        self._markers = markers
        self.outcome = "error"
        self._traced = False
        self._t0 = 0.0

    def __enter__(self):
        from django.conf import settings

        self._t0 = time.monotonic()
        if getattr(settings, "COMMAND_TRACE_ENABLED", True):
            begin_command_trace(
                caller=self._caller,
                session=self._session,
                raw_string=self._raw_string,
                cmd_key=self._cmd_key,
            )
            self._traced = True
        return self

    def __exit__(self, exc_type, exc, tb):
        from django.conf import settings

        elapsed = time.monotonic() - self._t0
        if self._metrics:
            try:
                from evennia.server.prometheus_metrics import record_action_input

                record_action_input(self.outcome, elapsed)
            except Exception:
                from evennia.utils import logger

                logger.log_trace("action input metric failed")
        if self._traced:
            end_command_trace()
        if self._markers and getattr(settings, "COMMAND_COMPLETION_MARKERS_ENABLED", False):
            session = self._session
            if session is not None and hasattr(session, "msg"):
                try:
                    marker = (
                        "\x1eEV-COMMAND-DONE "
                        f"outcome={self.outcome} elapsed_ms={elapsed * 1000.0:.3f}\x1f"
                    )
                    session.msg(marker, options={"raw": True})
                except Exception:
                    from evennia.utils import logger

                    logger.log_trace("CM1 completion marker delivery failed")
        return False


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
