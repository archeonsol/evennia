"""Bounded semantic delivery timeline and external narrative sink registry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "NarrativeSink",
    "CanonicalSink",
    "register_sink",
    "unregister_sink",
    "register_canonical_sink",
    "unregister_canonical_sink",
    "record_delivery",
    "record_event",
    "recent_deliveries",
    "recent_events",
]

MAX_TIMELINE_ENTRIES = 200
_SINKS = {}
_CANONICAL_SINKS = {}
_EVENTS = deque(maxlen=MAX_TIMELINE_ENTRIES)
_EVENT_IDS = set()


@runtime_checkable
class NarrativeSink(Protocol):
    """Consumer of immutable viewer-resolved RenderNodes."""

    def accept(self, node, viewer) -> None:
        """Consume ``node`` without mutating it or world state."""
        ...


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    """One bounded timeline entry."""

    node_id: str
    correlation_id: str
    kind: str
    msg_type: str
    body: str


@runtime_checkable
class CanonicalSink(Protocol):
    """Consumer of canonical, pre-resolution render plans.

    The seam recordings, cameras, forensics and replay want: a plan still holds
    its references, so a stored event re-resolves for whoever reads it later
    rather than freezing one viewer's reading of it.
    """

    def accept_event(self, plan) -> None:
        """Consume ``plan`` without mutating it or world state."""
        ...


def register_canonical_sink(key: str, sink: CanonicalSink, *, override: bool = False):
    """Register a recording, camera, forensic, or replay sink."""
    normalized = str(key or "").strip().lower()
    if not normalized:
        raise ValueError("canonical sink key is required")
    if normalized in _CANONICAL_SINKS and not override:
        raise ValueError(f"canonical sink already registered: {normalized}")
    if not isinstance(sink, CanonicalSink):
        raise TypeError("canonical sink must implement accept_event(plan)")
    _CANONICAL_SINKS[normalized] = sink
    return sink


def unregister_canonical_sink(key: str):
    """Remove and return a registered canonical sink."""
    return _CANONICAL_SINKS.pop(str(key or "").strip().lower(), None)


def record_event(plan) -> None:
    """Record one canonical event, once, before any viewer resolution."""
    plan_id = getattr(plan, "plan_id", None)
    if plan_id and plan_id in _EVENT_IDS:
        return
    if len(_EVENTS) == _EVENTS.maxlen:
        evicted = _EVENTS[0]
        evicted_id = getattr(evicted, "plan_id", None)
        if evicted_id:
            _EVENT_IDS.discard(evicted_id)
    _EVENTS.append(plan)
    if plan_id:
        _EVENT_IDS.add(plan_id)
    for sink in tuple(_CANONICAL_SINKS.values()):
        try:
            sink.accept_event(plan)
        except Exception:
            from evennia.utils import logger

            logger.log_trace("canonical narrative sink failed")


def recent_events(limit: int = 50) -> tuple:
    """Return the newest bounded canonical events in chronological order."""
    count = max(0, min(int(limit), MAX_TIMELINE_ENTRIES))
    return tuple(_EVENTS)[-count:]


def register_sink(key: str, sink: NarrativeSink, *, override: bool = False):
    """Register an accessibility, camera, replay, or telemetry sink."""
    normalized = str(key or "").strip().lower()
    if not normalized:
        raise ValueError("narrative sink key is required")
    if normalized in _SINKS and not override:
        raise ValueError(f"narrative sink already registered: {normalized}")
    if not isinstance(sink, NarrativeSink):
        raise TypeError("narrative sink must implement accept(node, viewer)")
    _SINKS[normalized] = sink
    return sink


def unregister_sink(key: str):
    """Remove and return a registered sink."""
    return _SINKS.pop(str(key or "").strip().lower(), None)


def _timeline(viewer, *, create: bool = False):
    ndb = getattr(viewer, "ndb", None)
    if ndb is None:
        return None
    timeline = getattr(ndb, "_render_timeline", None)
    if timeline is None and create:
        timeline = deque(maxlen=MAX_TIMELINE_ENTRIES)
        setattr(ndb, "_render_timeline", timeline)
    return timeline


def record_delivery(node, viewer) -> None:
    """Record one delivery and notify registered read-only sinks."""
    timeline = _timeline(viewer, create=True)
    if timeline is not None:
        timeline.append(
            DeliveryRecord(
                node_id=node.node_id,
                correlation_id=node.correlation_id,
                kind=node.kind,
                msg_type=node.msg_type,
                body=node.body,
            )
        )
    for sink in tuple(_SINKS.values()):
        try:
            sink.accept(node, viewer)
        except Exception:
            from evennia.utils import logger

            logger.log_trace("narrative sink failed")


def recent_deliveries(viewer, *, limit: int = 50) -> tuple[DeliveryRecord, ...]:
    """Return newest bounded semantic deliveries in chronological order."""
    timeline = _timeline(viewer)
    if not timeline:
        return ()
    count = max(0, min(int(limit), MAX_TIMELINE_ENTRIES))
    return tuple(timeline)[-count:]
