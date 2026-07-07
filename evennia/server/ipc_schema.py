"""Typed pydantic models for the Portal<->Server IPC envelopes.

This is the schema layer of the T3 modernization (Tier 1.3): it puts a typed,
validated face on the control- and data-plane messages that flow between the
Portal and the Server, regardless of which transport (legacy AMP TCP or the
redis Streams bus) actually carries the bytes.

It deliberately does NOT re-implement serialization. The wire bytes are still
produced and consumed by :mod:`evennia.server.amp_serde` (the hardened JSON
envelope: type-safety + pickle rejection + resource caps). These models are a
construction/validation convenience on top of that ``[sessid, kwargs]`` shape,
and the single source of truth for the admin-operation catalog.

Three message families share the ``[sessid, kwargs]`` envelope:

- **session** (``MsgPortal2Server`` / ``MsgServer2Portal``): the data plane,
  a session id plus a frame of outputfunc/inputfunc calls in ``kwargs``.
- **admin** (``AdminPortal2Server`` / ``AdminServer2Portal``): the control
  plane: a session id plus an ``operation`` (see :class:`AdminOperation`) plus
  operation-specific kwargs.

``sessid`` 0 is the reserved dummy session used for broadcast/control ops.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evennia.server import amp_serde

# --------------------------------------------------------------------------- #
# Admin operation catalog
#
# The single-char codes are the historical AMP admin operation constants (see
# evennia/server/portal/amp.py). They stay chr(N) on the wire for compatibility;
# this enum gives them names and a typed home. Keep in sync with amp.py.
# --------------------------------------------------------------------------- #


class AdminOperation(str, Enum):
    """Portal<->Server control-plane operations."""

    PCONN = chr(1)  # portal session connect
    PDISCONN = chr(2)  # portal session disconnect
    PSYNC = chr(3)  # portal session sync
    SLOGIN = chr(4)  # server session login
    SDISCONN = chr(5)  # server session disconnect
    SDISCONNALL = chr(6)  # server session disconnect all
    SSYNC = chr(8)  # server session sync
    SCONN = chr(11)  # server creating new connection (irc bots etc.)
    PCONNSYNC = chr(12)  # portal post-syncing a session
    PDISCONNALL = chr(13)  # portal session disconnect all
    SRELOAD = chr(14)  # server shutdown in reload mode
    SSTART = chr(15)  # server start
    PSHUTD = chr(16)  # portal (+server) shutdown
    SSHUTD = chr(17)  # server shutdown
    PSTATUS = chr(18)  # ping server or portal status
    SRESET = chr(19)  # server shutdown in reset mode

    @classmethod
    def coerce(cls, value: "AdminOperation | str") -> "AdminOperation":
        """Accept an enum member or its raw single-char wire code."""
        if isinstance(value, cls):
            return value
        return cls(value)


DUMMYSESSID = 0


# --------------------------------------------------------------------------- #
# Envelope models
# --------------------------------------------------------------------------- #


class SessionEnvelope(BaseModel):
    """Data-plane message: a session id plus a frame of outputfunc calls.

    ``kwargs`` maps each outputfunc/inputfunc name to its ``[args, kwargs]``
    payload (e.g. ``{"text": [["hello"], {}]}``). It is kept as a plain dict so
    the per-outputfunc schema (see :mod:`evennia.server.protocol`) can validate
    the individual frames without this envelope having to know every command.
    """

    model_config = ConfigDict(frozen=True)

    sessid: int = Field(ge=0)
    kwargs: Dict[str, Any] = Field(default_factory=dict)

    def to_wire(self) -> bytes:
        return amp_serde.pack_session_message(self.sessid, self.kwargs)

    @classmethod
    def from_wire(cls, data: bytes, *, enforce_limits: bool = True) -> "SessionEnvelope":
        sessid, kwargs = amp_serde.unpack_session_message(data, enforce_limits=enforce_limits)
        return cls(sessid=sessid, kwargs=kwargs)


class AdminEnvelope(BaseModel):
    """Control-plane message: a session id, an operation, and its kwargs.

    On the wire the operation travels as an ``"operation"`` key inside the
    packed kwargs; this model lifts it into a typed field and re-inserts it on
    :meth:`to_wire`.
    """

    model_config = ConfigDict(frozen=True)

    sessid: int = Field(ge=0)
    operation: AdminOperation
    kwargs: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("operation", mode="before")
    @classmethod
    def _coerce_operation(cls, value):
        return AdminOperation.coerce(value)

    def to_wire(self) -> bytes:
        payload = dict(self.kwargs)
        payload["operation"] = self.operation.value
        return amp_serde.pack_admin_message(self.sessid, payload)

    @classmethod
    def from_wire(cls, data: bytes) -> "AdminEnvelope":
        sessid, kwargs = amp_serde.unpack_admin_message(data)
        kwargs = dict(kwargs)
        operation = kwargs.pop("operation", None)
        if operation is None:
            raise ValueError("admin envelope missing 'operation'")
        return cls(sessid=sessid, operation=operation, kwargs=kwargs)


def parse_session(data: bytes, *, enforce_limits: bool = True) -> Tuple[int, dict]:
    """Validate + unpack a session message, returning ``(sessid, kwargs)``."""
    env = SessionEnvelope.from_wire(data, enforce_limits=enforce_limits)
    return env.sessid, env.kwargs


def parse_admin(data: bytes) -> Tuple[int, str, dict]:
    """Validate + unpack an admin message, returning ``(sessid, op, kwargs)``.

    ``op`` is the single-char wire code (``str``), matching ``amp.PSYNC`` etc.
    """
    env = AdminEnvelope.from_wire(data)
    return env.sessid, env.operation.value, env.kwargs
