"""Immutable universal narrative nodes and capability-aware delivery.

Every viewer-facing surface can normalize to :class:`RenderNode`. Text-only
clients receive the parity-anchor ``body`` while structured clients receive a
versioned tree with opaque, viewer-scoped references. Engine internals may keep
viewer-invariant spans containing database identifiers, but wire payloads never
serialize those identifiers.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

__all__ = [
    "CLIENT_NARRATIVE_FLAG",
    "RENDER_SCHEMA",
    "EntityRef",
    "Line",
    "Paragraph",
    "Section",
    "ListBlock",
    "SystemBlock",
    "RenderNode",
    "text_node",
    "deliver_node",
]

CLIENT_NARRATIVE_FLAG = "CLIENT_NARRATIVE"
RENDER_SCHEMA = "render.v1"
MAX_BODY_CHARS = 128 * 1024
MAX_REFS = 256
MAX_BLOCKS = 256
MAX_METADATA_ITEMS = 64
_FORBIDDEN_WIRE_KEYS = frozenset({"char_id", "from_id", "referent_id", "object_id"})


def _primitive(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-safe copy of ``value``.

    Raises:
        TypeError: If ``value`` contains a live object or unsupported type.
        ValueError: If the structure exceeds its depth or collection limits.
    """
    if depth > 8:
        raise ValueError("render metadata exceeds maximum depth")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_BODY_CHARS]
    if isinstance(value, Mapping):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("render mapping exceeds item limit")
        return {str(key)[:128]: _primitive(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_BLOCKS:
            raise ValueError("render collection exceeds item limit")
        return [_primitive(item, depth=depth + 1) for item in value]
    raise TypeError(f"render payload contains unsupported value {type(value).__name__}")


def _contains_forbidden_identity(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in _FORBIDDEN_WIRE_KEYS or _contains_forbidden_identity(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_identity(item) for item in value)
    return False


def _deep_freeze(value: Any) -> Any:
    """Freeze nested JSON-safe data so a node is immutable by construction."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class EntityRef:
    """An interactive entity reference safe to expose to one viewer.

    ``handle`` is opaque and resolves only in the issuing viewer's context.
    ``label`` is the exact presentation the viewer was authorized to perceive.
    """

    handle: str
    label: str
    kind: str = "entity"
    recognized: bool = False
    affordances: tuple[str, ...] = ()
    role: str = "target"

    def __post_init__(self):
        object.__setattr__(self, "affordances", tuple(str(item) for item in self.affordances))

    def payload(self) -> dict:
        """Return the public wire representation."""
        return {
            "handle": self.handle[:128],
            "label": self.label[:4096],
            "name": self.label[:4096],  # compatibility for the current shell
            "kind": self.kind[:64],
            "recognized": bool(self.recognized),
            "affordances": [str(item)[:64] for item in self.affordances[:32]],
            "role": self.role[:64],
        }


@dataclass(frozen=True, slots=True)
class Line:
    """A single display line."""

    text: str
    style: str = ""

    def payload(self) -> dict:
        return {"type": "line", "text": self.text[:MAX_BODY_CHARS], "style": self.style[:64]}


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A paragraph block."""

    text: str
    style: str = ""

    def payload(self) -> dict:
        return {
            "type": "paragraph",
            "text": self.text[:MAX_BODY_CHARS],
            "style": self.style[:64],
        }


@dataclass(frozen=True, slots=True)
class Section:
    """A named block containing ordered child blocks."""

    key: str
    title: str = ""
    children: tuple[Any, ...] = ()
    style: str = ""

    def __post_init__(self):
        object.__setattr__(self, "children", tuple(self.children))

    def payload(self) -> dict:
        return {
            "type": "section",
            "key": self.key[:64],
            "title": self.title[:4096],
            "style": self.style[:64],
            "children": [_block_payload(child) for child in self.children[:MAX_BLOCKS]],
        }


@dataclass(frozen=True, slots=True)
class ListBlock:
    """A semantic ordered or unordered list."""

    items: tuple[str, ...]
    ordered: bool = False
    style: str = ""

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(str(item) for item in self.items))

    def payload(self) -> dict:
        return {
            "type": "list",
            "ordered": bool(self.ordered),
            "items": [str(item)[:4096] for item in self.items[:MAX_BLOCKS]],
            "style": self.style[:64],
        }


@dataclass(frozen=True, slots=True)
class SystemBlock:
    """A machine-classifiable notice or status block."""

    text: str
    level: str = "info"
    code: str = ""

    def payload(self) -> dict:
        return {
            "type": "system",
            "text": self.text[:MAX_BODY_CHARS],
            "level": self.level[:32],
            "code": self.code[:128],
        }


def _block_payload(block: Any) -> dict:
    if hasattr(block, "payload"):
        data = block.payload()
    elif isinstance(block, str):
        data = Line(block).payload()
    else:
        raise TypeError(f"unsupported render block {type(block).__name__}")
    return _primitive(data)


def _coerce_ref(ref: EntityRef | Mapping) -> EntityRef:
    if isinstance(ref, EntityRef):
        return ref
    if not isinstance(ref, Mapping):
        raise TypeError("RenderNode refs must be EntityRef or mappings")
    if any(key in ref for key in _FORBIDDEN_WIRE_KEYS):
        raise ValueError("raw database identity is forbidden in RenderNode refs")
    return EntityRef(
        handle=str(ref.get("handle") or ""),
        label=str(ref.get("label") or ref.get("name") or ""),
        kind=str(ref.get("kind") or "entity"),
        recognized=bool(ref.get("recognized", False)),
        affordances=tuple(str(item) for item in ref.get("affordances") or ()),
        role=str(ref.get("role") or "target"),
    )


@dataclass(frozen=True, slots=True)
class RenderNode:
    """One immutable, versioned unit of viewer-resolved output."""

    kind: str
    msg_type: str
    body: str
    from_handle: str | None = None
    refs: tuple[EntityRef, ...] = ()
    self_echo: bool = False
    spans: tuple[tuple[Any, ...], ...] | None = None
    blocks: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    node_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema: str = RENDER_SCHEMA

    def __post_init__(self):
        if not self.kind or len(self.kind) > 64:
            raise ValueError("RenderNode kind must contain 1-64 characters")
        if len(self.body) > MAX_BODY_CHARS:
            raise ValueError("RenderNode body exceeds maximum length")
        refs = tuple(_coerce_ref(ref) for ref in self.refs)
        if len(refs) > MAX_REFS:
            raise ValueError("RenderNode has too many entity references")
        blocks = tuple(self.blocks)
        if len(blocks) > MAX_BLOCKS:
            raise ValueError("RenderNode has too many blocks")
        spans = None if self.spans is None else tuple(tuple(segment) for segment in self.spans)
        metadata = _deep_freeze(_primitive(dict(self.metadata)))
        object.__setattr__(self, "refs", refs)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "spans", spans)
        object.__setattr__(self, "metadata", metadata)

    def with_refs(self, refs) -> "RenderNode":
        """Return a copy carrying lazily built viewer references."""
        return replace(self, refs=tuple(refs))

    def payload(self) -> dict:
        """Return the bounded public wire payload.

        Viewer-invariant spans may contain server database identifiers. They are
        serialized only when their representation is identity-safe; otherwise
        the structured blocks/body remain available and metadata notes the
        redaction. Use :meth:`storage_payload` for trusted server-side replay.
        """
        metadata = dict(self.metadata)
        data = {
            "schema": self.schema,
            "node_id": self.node_id,
            "correlation_id": self.correlation_id,
            "kind": self.kind,
            "msg_type": self.msg_type,
            "body": self.body,
            "from_handle": self.from_handle,
            "refs": [ref.payload() for ref in self.refs],
            "self_echo": self.self_echo,
            "blocks": [_block_payload(block) for block in self.blocks],
            "metadata": metadata,
        }
        if self.spans is not None:
            from evennia.narrative.render import span_to_dict

            serialized = [[span_to_dict(span) for span in segment] for segment in self.spans]
            if _contains_forbidden_identity(serialized):
                data["metadata"] = {**metadata, "structure_redacted": True}
            else:
                data["spans"] = serialized
        return _primitive(data)

    def storage_payload(self) -> dict:
        """Return a trusted server-side payload retaining invariant spans."""
        data = self.payload()
        if self.spans is not None:
            from evennia.narrative.render import span_to_dict

            data["spans"] = [[span_to_dict(span) for span in segment] for segment in self.spans]
        return data


def text_node(text: str, *, msg_type: str = "text", kind: str = "text", **kwargs) -> RenderNode:
    """Normalize legacy text into a universal node."""
    return RenderNode(
        kind=kind,
        msg_type=msg_type,
        body=str(text),
        blocks=(Line(str(text)),),
        **kwargs,
    )


def _sessions(viewer):
    handler = getattr(viewer, "sessions", None)
    if handler is None:
        return []
    try:
        return list(handler.all())
    except Exception:
        from evennia.utils import logger

        logger.log_trace()
        return []


def _supports_nodes(session) -> bool:
    flags = getattr(session, "protocol_flags", None) or {}
    caps = flags.get("AZABAN_CAPS") or {}
    return bool(flags.get(CLIENT_NARRATIVE_FLAG) or caps.get("rendersNodes"))


def deliver_node(
    node: RenderNode,
    viewer,
    from_obj=None,
    refs_builder=None,
    sessions=None,
    options=None,
):
    """Deliver one node to every viewer session with text parity fallback."""
    started = time.perf_counter()
    target_sessions = list(sessions) if sessions is not None else _sessions(viewer)
    capable = [session for session in target_sessions if _supports_nodes(session)]
    delivered = node
    if capable and refs_builder is not None and not node.refs:
        delivered = node.with_refs(refs_builder())
    try:
        from evennia.narrative.timeline import record_delivery

        record_delivery(delivered, viewer)
    except Exception:
        from evennia.utils import logger

        logger.log_trace("render timeline sink failed")
    if not capable:
        viewer.msg(
            (node.body, {"type": node.msg_type}),
            from_obj=from_obj,
            options=options,
            _render_delivery=True,
        )
        _record_delivery_metric("text", started)
        return
    viewer.msg(
        narrative=([delivered.payload()], {}),
        session=capable,
        from_obj=from_obj,
        options=options,
        _render_delivery=True,
    )
    others = [session for session in target_sessions if session not in capable]
    if others:
        viewer.msg(
            (node.body, {"type": node.msg_type}),
            session=others,
            from_obj=from_obj,
            options=options,
            _render_delivery=True,
        )
    _record_delivery_metric("mixed" if others else "structured", started)


def _record_delivery_metric(mode: str, started: float) -> None:
    """Record optional engine metrics without coupling delivery to Prometheus."""
    try:
        from evennia.server.prometheus_metrics import record_render_delivery

        record_render_delivery(mode, time.perf_counter() - started)
    except Exception:
        pass
