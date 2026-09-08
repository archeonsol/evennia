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
    "flatten_blocks",
    "narrative_mode",
    "MODE_OFF",
    "MODE_NODES",
    "MODE_BOTH",
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
    """A single display line.

    ``spans`` is the viewer-invariant inline tree behind ``text``. A canonical
    :class:`~evennia.narrative.plan.RenderPlan` carries blocks with ``spans``
    and an empty ``text``; resolution fills ``text`` per viewer and drops the
    spans. Both are never authored independently -- that is the invariant that
    keeps body, blocks, and structure from disagreeing.
    """

    text: str = ""
    style: str = ""
    spans: tuple[Any, ...] | None = None

    def __post_init__(self):
        if self.spans is not None:
            object.__setattr__(self, "spans", tuple(self.spans))

    def payload(self) -> dict:
        return {"type": "line", "text": self.text[:MAX_BODY_CHARS], "style": self.style[:64]}


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A paragraph block. See :class:`Line` for the ``spans``/``text`` contract."""

    text: str = ""
    style: str = ""
    spans: tuple[Any, ...] | None = None

    def __post_init__(self):
        if self.spans is not None:
            object.__setattr__(self, "spans", tuple(self.spans))

    def payload(self) -> dict:
        return {
            "type": "paragraph",
            "text": self.text[:MAX_BODY_CHARS],
            "style": self.style[:64],
        }


@dataclass(frozen=True, slots=True)
class Section:
    """A named block containing ordered child blocks.

    ``sep`` is the string that joins this section's children when the tree is
    flattened for a text client. Nesting sections with different ``sep`` values
    reproduces grouped layouts (a room look's blank-line-separated groups of
    newline-separated lines) without a separate layout spec.
    """

    key: str
    title: str = ""
    children: tuple[Any, ...] = ()
    style: str = ""
    sep: str = "\n"

    def __post_init__(self):
        object.__setattr__(self, "children", tuple(self.children))

    def payload(self) -> dict:
        return {
            "type": "section",
            "key": self.key[:64],
            "title": self.title[:4096],
            "style": self.style[:64],
            "children": [_block_payload(child) for child in self.children],
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
            "items": [str(item)[:4096] for item in self.items],
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


def _validate_block_counts(blocks):
    """Reject excess blocks and list items before serialization can lose them."""
    count = 0
    pending = list(blocks)
    while pending:
        block = pending.pop()
        count += 1
        if count > MAX_BLOCKS:
            raise ValueError("RenderNode has too many blocks")
        if isinstance(block, Section):
            pending.extend(block.children)
        elif isinstance(block, ListBlock) and len(block.items) > MAX_BLOCKS:
            raise ValueError("RenderNode list has too many items")


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
    sep: str = "\n"
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
        _validate_block_counts(blocks)
        if blocks and self.body != flatten_blocks(blocks, self.sep):
            raise ValueError("RenderNode body must be derived from its blocks")
        spans = None if self.spans is None else tuple(tuple(segment) for segment in self.spans)
        metadata = _deep_freeze(_primitive(dict(self.metadata)))
        object.__setattr__(self, "refs", refs)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "spans", spans)
        object.__setattr__(self, "metadata", metadata)

    def with_refs(self, refs) -> "RenderNode":
        """Return a copy carrying lazily built viewer references."""
        return replace(self, refs=tuple(refs))

    def map_text(self, transform) -> "RenderNode":
        """Transform every visible text leaf and re-derive the flattened body.

        Universal transforms must use this instead of replacing ``body`` alone.
        It preserves text/structured-client parity by making the block tree the
        source of truth. Nodes without blocks are legacy scalar nodes and map
        their body directly.

        Args:
            transform (callable): ``str -> str`` mapper.

        Returns:
            RenderNode: A coherent transformed copy.
        """
        if not self.blocks:
            return replace(self, body=str(transform(self.body)))
        blocks = tuple(_map_block_text(block, transform) for block in self.blocks)
        return replace(self, body=flatten_blocks(blocks, self.sep), blocks=blocks)

    def prepend_text(self, prefix: str) -> "RenderNode":
        """Prepend text to the first visible leaf and re-derive ``body``."""
        prefix = str(prefix or "")
        if not prefix:
            return self
        if not self.blocks:
            return replace(self, body=prefix + self.body)
        blocks = list(self.blocks)
        for index, block in enumerate(blocks):
            mapped, changed = _prepend_block_text(block, prefix)
            blocks[index] = mapped
            if changed:
                break
        else:
            blocks.insert(0, Line(prefix))
        frozen = tuple(blocks)
        return replace(self, body=flatten_blocks(frozen, self.sep), blocks=frozen)

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
            "sep": self.sep,
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


def _map_block_text(block, transform):
    """Return one block with all visible strings mapped."""
    if isinstance(block, str):
        return str(transform(block))
    if isinstance(block, Section):
        return replace(
            block,
            title=str(transform(block.title)) if block.title else "",
            children=tuple(_map_block_text(child, transform) for child in block.children),
        )
    if isinstance(block, ListBlock):
        return replace(block, items=tuple(str(transform(item)) for item in block.items))
    if isinstance(block, (Line, Paragraph, SystemBlock)):
        return replace(block, text=str(transform(block.text)))
    raise TypeError(f"unsupported render block {type(block).__name__}")


def _prepend_block_text(block, prefix):
    """Return ``(block, changed)`` after prefixing its first visible leaf."""
    if isinstance(block, str):
        return prefix + block, True
    if isinstance(block, Section):
        if block.title:
            return replace(block, title=prefix + block.title), True
        children = list(block.children)
        for index, child in enumerate(children):
            mapped, changed = _prepend_block_text(child, prefix)
            children[index] = mapped
            if changed:
                return replace(block, children=tuple(children)), True
        return block, False
    if isinstance(block, ListBlock):
        if not block.items:
            return block, False
        return replace(block, items=(prefix + block.items[0],) + block.items[1:]), True
    if isinstance(block, (Line, Paragraph, SystemBlock)):
        return replace(block, text=prefix + block.text), True
    raise TypeError(f"unsupported render block {type(block).__name__}")


def flatten_blocks(blocks, sep: str = "\n") -> str:
    """Flatten a resolved block tree into one markup string.

    This is the text-client half of R1: the same resolved structure that a rich
    client receives as a tree becomes the line a telnet client receives. It
    emits **Evennia markup** (``|r``...), never ANSI escapes or HTML -- the
    portal's protocol layer owns that conversion, so one flatten result serves
    xterm256, no-colour, and screenreader sessions alike.

    Empty blocks vanish rather than contributing a blank separator, matching
    :meth:`evennia.narrative.render.SectionedView.flatten`.

    Args:
        blocks (iterable): resolved blocks (no unresolved spans).
        sep (str): separator joining this level's non-empty results. Nested
            :class:`Section` blocks join their own children with their ``sep``.

    Returns:
        str: the flattened markup string.
    """
    parts = []
    for block in blocks:
        if isinstance(block, str):
            text = block
        elif isinstance(block, Section):
            text = flatten_blocks(block.children, block.sep)
            if block.title and text:
                text = f"{block.title}{block.sep}{text}"
            elif block.title:
                text = block.title
        elif isinstance(block, ListBlock):
            text = "\n".join(item for item in block.items if item)
        else:
            text = getattr(block, "text", "")
        if text:
            parts.append(text)
    return sep.join(parts)


# -- client capability tiers -------------------------------------------------
#
# A client is not simply "structured" or "legacy". A third-party client may want
# the text line it has always rendered *and* the node tree as a sidecar (the
# MSDP-room-info pattern), and a client that announced node support over a
# transport that cannot carry nodes must not be left with silence.

MODE_OFF = "off"
MODE_NODES = "nodes"
MODE_BOTH = "both"
_MODES = frozenset({MODE_OFF, MODE_NODES, MODE_BOTH})


def _has_structured_transport(session) -> bool:
    """Whether this session's transport can carry a nested node payload.

    MSDP encodes flat tables and arrays; a ``render.v1`` tree does not survive
    it. GMCP (JSON) and the websocket wire formats do. A session with neither
    OOB flag is a web/shell protocol, which carries structure natively.
    """
    flags = getattr(session, "protocol_flags", None) or {}
    if flags.get("OOB_MSDP") and not flags.get("OOB_GMCP"):
        return False
    return True


def narrative_mode(session) -> str:
    """Return this session's narrative delivery tier.

    ``off`` -- text only (every un-upgraded client). ``nodes`` -- structured
    payload only. ``both`` -- text line *and* structured sidecar, for a
    third-party client that renders text natively and treats nodes as
    enrichment; also the safe tier for a client whose node rendering may fail,
    since the text line is never withheld.
    """
    flags = getattr(session, "protocol_flags", None) or {}
    declared = flags.get(CLIENT_NARRATIVE_FLAG)
    if isinstance(declared, str):
        mode = declared.strip().lower()
        if mode not in _MODES:
            mode = MODE_OFF
    elif declared:
        mode = MODE_NODES  # legacy boolean opt-in
    else:
        caps = flags.get("AZABAN_CAPS") or {}
        mode = MODE_NODES if caps.get("rendersNodes") else MODE_OFF
    if mode != MODE_OFF and not _has_structured_transport(session):
        return MODE_OFF
    return mode


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
    """Whether this session receives the structured payload at all."""
    return narrative_mode(session) in (MODE_NODES, MODE_BOTH)


def deliver_node(
    node: RenderNode,
    viewer,
    from_obj=None,
    refs_builder=None,
    sessions=None,
    options=None,
    _transformed=False,
    transform_context=None,
    **msg_kwargs,
):
    """Deliver one node to every viewer session with text parity fallback.

    Each session receives what its tier declares (see :func:`narrative_mode`):
    the flattened ``body`` line, the structured payload, or both. The node is
    recorded on the semantic timeline exactly once regardless of tier, so a
    telnet-only recipient is as visible to sinks as a shell one.
    """
    if not _transformed:
        from evennia.narrative.plan import deliver_resolved

        return deliver_resolved(
            node,
            viewer,
            from_obj=from_obj,
            sessions=sessions,
            options=options,
            context=transform_context,
            refs_builder=refs_builder,
            **msg_kwargs,
        )

    started = time.perf_counter()
    explicit_sessions = sessions is not None
    target_sessions = list(sessions) if explicit_sessions else _sessions(viewer)
    modes = {id(session): narrative_mode(session) for session in target_sessions}
    capable = [session for session in target_sessions if modes[id(session)] != MODE_OFF]
    delivered = node
    # Entity refs are a rich-client wire artifact; a text-only recipient never
    # sees them, so the (possibly expensive) builder stays lazy.
    if capable and refs_builder is not None and not node.refs:
        delivered = node.with_refs(refs_builder())
    try:
        from evennia.narrative.timeline import record_delivery

        record_delivery(delivered, viewer)
    except Exception:
        from evennia.utils import logger

        logger.log_trace("render timeline sink failed")
    text_meta = _text_metadata(node)
    if not capable:
        # Preserve an explicitly targeted session subset; without one, let the
        # recipient's own multisession policy decide, exactly as before R1.
        if explicit_sessions and target_sessions:
            msg_kwargs = {**msg_kwargs, "session": target_sessions}
        viewer.msg(
            (node.body, text_meta),
            from_obj=from_obj,
            options=options,
            _render_delivery=True,
            **msg_kwargs,
        )
        _record_delivery_metric("text", started)
        return
    viewer.msg(
        narrative=([delivered.payload()], {}),
        session=capable,
        from_obj=from_obj,
        options=options,
        _render_delivery=True,
        **msg_kwargs,
    )
    # MODE_BOTH sessions take the structured payload *and* the text line.
    others = [session for session in target_sessions if modes[id(session)] != MODE_NODES]
    if others:
        viewer.msg(
            (node.body, text_meta),
            session=others,
            from_obj=from_obj,
            options=options,
            _render_delivery=True,
            **msg_kwargs,
        )
    _record_delivery_metric("mixed" if others else "structured", started)


def _text_metadata(node: RenderNode) -> dict:
    """Rebuild the ``(text, {...})`` metadata dict a text client expects.

    A legacy caller may pass outputfunc metadata beyond ``type``; normalizing
    every message through the node core must not drop it, so it rides in the
    node's metadata under ``text_kwargs`` and is restored here.
    """
    extra = node.metadata.get("text_kwargs") if node.metadata else None
    meta = {"type": node.msg_type}
    if isinstance(extra, Mapping):
        meta.update({str(key): value for key, value in extra.items() if key != "type"})
    return meta


def _record_delivery_metric(mode: str, started: float) -> None:
    """Record optional engine metrics without coupling delivery to Prometheus."""
    try:
        from evennia.server.prometheus_metrics import record_render_delivery

        record_render_delivery(mode, time.perf_counter() - started)
    except Exception:
        pass
