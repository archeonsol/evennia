"""The canonical render plan: one viewer-invariant event, resolved per viewer.

This is the seam the rest of R1 rests on. Two immutable types, not one:

- :class:`RenderPlan` -- the **canonical** event. Built once, by whoever caused
  it. Its blocks carry *references* (characters, exits, items, senders, speech),
  not names. It is viewer-invariant: the same plan is the truth for every
  recipient, for the recording, for the camera relay, and for the replay.
- :class:`~evennia.narrative.rendernode.RenderNode` -- the **delivered** result.
  Produced by :func:`resolve` for one viewer: references have become the text
  that viewer was authorized to perceive, and identity survives only as opaque
  per-viewer handles.

Keeping the two apart is what stops viewer-resolved output being stored as
though it were canonical structure. A producer that returns a ``RenderPlan``
cannot accidentally bake a name into it, because there is nowhere to put one:
``body`` does not exist until :func:`resolve` derives it.

Text parity is a property of the same path, not a separate one. ``resolve``
flattens the resolved blocks into ``body`` via
:func:`~evennia.narrative.rendernode.flatten_blocks`, so a telnet session and a
shell session are two renderings of one resolution rather than two pipelines.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from evennia.narrative.render import (
    CharRef,
    KeyResolver,
    ObjectRef,
    PronounRef,
    SelfRef,
    SenderRef,
    SpeechSpan,
    TextSpan,
    ViewerContext,
)
from evennia.narrative.rendernode import (
    MAX_BLOCKS,
    MAX_BODY_CHARS,
    MAX_REFS,
    EntityRef,
    Line,
    ListBlock,
    Paragraph,
    RenderNode,
    Section,
    SystemBlock,
    _deep_freeze,
    _primitive,
    flatten_blocks,
)

__all__ = [
    "RenderPlan",
    "resolve",
    "deliver",
    "deliver_to",
    "deliver_resolved",
    "frame_plan",
    "text_plan",
    "new_correlation_id",
    "set_span_resolver",
    "get_span_resolver",
    "set_entity_lookup",
]

_RESOLVER = None
_LOOKUP = None


def new_correlation_id() -> str:
    """Return a fresh id tying every viewer's rendering of one event together."""
    return uuid.uuid4().hex


def set_span_resolver(resolver):
    """Install the game's :class:`~evennia.narrative.render.SpanResolver`.

    One resolver, set at startup, is what makes naming/perception/language/
    psychosis run exactly once per delivery instead of being wired per surface.
    """
    global _RESOLVER
    _RESOLVER = resolver
    return resolver


def get_span_resolver():
    """Return the installed resolver, or the engine's key-naming default."""
    if _RESOLVER is not None:
        return _RESOLVER
    return KeyResolver(lookup=_entity)


def set_entity_lookup(fn):
    """Install ``fn(entity_id) -> object`` used to mint handles from refs.

    Also becomes the engine resolver's default lookup, so default naming and
    handle minting always agree about which entity an id denotes.
    """
    global _LOOKUP
    from evennia.narrative.render import set_default_lookup

    _LOOKUP = fn
    set_default_lookup(fn)
    return fn


def _entity(entity_id):
    """Resolve a dbref id to an object (server-side only; never crosses a wire)."""
    if entity_id is None:
        return None
    if _LOOKUP is not None:
        return _LOOKUP(entity_id)
    try:
        from evennia.objects.models import ObjectDB

        # ``get_id`` goes through Evennia's idmapper manager, so hot narrative
        # resolution reuses the in-process singleton instead of issuing one ORM
        # query per reference per viewer.
        return ObjectDB.objects.get_id(entity_id)
    except Exception:
        return None


def _ref_identity(span):
    """Return ``(entity_id, kind, role)`` for a span that names an entity."""
    if isinstance(span, CharRef):
        return span.char_id, "character", span.role
    if isinstance(span, ObjectRef):
        return span.object_id, span.kind, span.role
    if isinstance(span, SenderRef):
        return span.sender_id, "sender", "emitter"
    return None, "", ""


@dataclass(frozen=True, slots=True)
class RenderPlan:
    """One canonical, viewer-invariant event.

    Args:
        kind (str): the producer key this plan came from (``"say"``, ``"look"``,
            ``"attack"``), also the node's kind.
        msg_type (str): the message type text clients receive.
        blocks (tuple): block tree whose :class:`Line`/:class:`Paragraph` leaves
            carry ``spans``. Authoring ``text`` on a leaf is legal only for
            content with no identity in it (a system notice).
        sep (str): separator joining top-level blocks when flattened.
        subject_id: dbref of the emitter, if any. Server-side only; delivery
            turns it into a per-viewer ``from_handle``.
        metadata (Mapping): bounded JSON-safe facts about the event.
        correlation_id (str): assigned once, carried by every viewer's node and
            every stored artifact derived from this event.
    """

    kind: str
    msg_type: str = "text"
    blocks: tuple[Any, ...] = ()
    sep: str = "\n"
    subject_id: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=new_correlation_id)
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self):
        if not self.kind or len(self.kind) > 64:
            raise ValueError("RenderPlan kind must contain 1-64 characters")
        if not self.msg_type or len(self.msg_type) > 64:
            raise ValueError("RenderPlan msg_type must contain 1-64 characters")
        if len(self.sep) > 4096:
            raise ValueError("RenderPlan separator is too long")
        blocks = tuple(self.blocks)
        _validate_plan_blocks(blocks)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(
            self,
            "metadata",
            _deep_freeze(_primitive(dict(self.metadata))),
        )

    def spans(self):
        """Yield every span in the plan, depth-first in reading order."""
        yield from _walk_spans(self.blocks)

    def entity_ids(self):
        """Return the distinct entity ids this plan references (server-side)."""
        found = []
        for span in self.spans():
            entity_id, _kind, _role = _ref_identity(span)
            if entity_id is not None and entity_id not in found:
                found.append(entity_id)
        return tuple(found)

    def resolve(self, viewer, **kwargs) -> RenderNode:
        """Resolve this plan for ``viewer``. See :func:`resolve`."""
        return resolve(self, viewer, **kwargs)

    def storage_payload(self) -> dict:
        """Return the trusted, replayable canonical form.

        This is the one storage representation for cameras, photographs,
        recordings, forensic buffers, and replay. It retains references, so a
        stored event re-resolves for whoever reads it later -- recognition and
        perception apply retroactively rather than being frozen at capture.
        """
        from evennia.narrative.render import span_to_dict

        return {
            "schema": "renderplan.v1",
            "plan_id": self.plan_id,
            "correlation_id": self.correlation_id,
            "kind": self.kind,
            "msg_type": self.msg_type,
            "sep": self.sep,
            "subject_id": self.subject_id,
            "metadata": dict(self.metadata),
            "blocks": [_block_storage(block, span_to_dict) for block in self.blocks],
        }

    @classmethod
    def from_storage(cls, data: dict) -> "RenderPlan":
        """Rebuild a plan from :meth:`storage_payload` output."""
        from evennia.narrative.render import span_from_dict

        return cls(
            kind=str(data.get("kind") or "text"),
            msg_type=str(data.get("msg_type") or "text"),
            blocks=tuple(
                _block_from_storage(block, span_from_dict) for block in data.get("blocks") or ()
            ),
            sep=str(data.get("sep", "\n")),
            subject_id=data.get("subject_id"),
            metadata=dict(data.get("metadata") or {}),
            correlation_id=str(data.get("correlation_id") or ""),
            plan_id=str(data.get("plan_id") or uuid.uuid4().hex),
        )


def _walk_spans(blocks):
    for block in blocks:
        if isinstance(block, Section):
            yield from _walk_spans(block.children)
        else:
            for span in getattr(block, "spans", None) or ():
                yield span


def _validate_plan_blocks(blocks):
    """Validate the full canonical tree before any viewer resolves it."""
    count = 0
    for block in blocks:
        count += 1
        if count > MAX_BLOCKS:
            raise ValueError("RenderPlan has too many blocks")
        if isinstance(block, Section):
            if len(block.title) > MAX_BODY_CHARS or len(block.sep) > 4096:
                raise ValueError("RenderPlan section text is too long")
            count += _validate_plan_blocks(block.children)
            if count > MAX_BLOCKS:
                raise ValueError("RenderPlan has too many blocks")
            continue
        if isinstance(block, ListBlock):
            if len(block.items) > MAX_BLOCKS:
                raise ValueError("RenderPlan list has too many items")
            if any(len(item) > MAX_BODY_CHARS for item in block.items):
                raise ValueError("RenderPlan list item is too long")
            continue
        if isinstance(block, SystemBlock):
            if len(block.text) > MAX_BODY_CHARS:
                raise ValueError("RenderPlan system block is too long")
            continue
        if not isinstance(block, (Line, Paragraph)):
            raise TypeError(f"unsupported RenderPlan block {type(block).__name__}")
        if len(block.text) > MAX_BODY_CHARS:
            raise ValueError("RenderPlan text block is too long")
        spans = block.spans
        if spans is not None and len(spans) > MAX_REFS:
            raise ValueError("RenderPlan block has too many spans")
    return count


def _block_storage(block, span_to_dict):
    if isinstance(block, Section):
        return {
            "type": "section",
            "key": block.key,
            "title": block.title,
            "style": block.style,
            "sep": block.sep,
            "children": [_block_storage(child, span_to_dict) for child in block.children],
        }
    if isinstance(block, ListBlock):
        return {"type": "list", "items": list(block.items), "ordered": block.ordered}
    if isinstance(block, SystemBlock):
        return {"type": "system", "text": block.text, "level": block.level, "code": block.code}
    data = {
        "type": "paragraph" if isinstance(block, Paragraph) else "line",
        "text": getattr(block, "text", ""),
        "style": getattr(block, "style", ""),
    }
    spans = getattr(block, "spans", None)
    if spans is not None:
        data["spans"] = [span_to_dict(span) for span in spans]
    return data


def _block_from_storage(data, span_from_dict):
    kind = data.get("type")
    if kind == "section":
        return Section(
            key=str(data.get("key") or ""),
            title=str(data.get("title") or ""),
            children=tuple(
                _block_from_storage(child, span_from_dict) for child in data.get("children") or ()
            ),
            style=str(data.get("style") or ""),
            sep=str(data.get("sep", "\n")),
        )
    if kind == "list":
        return ListBlock(items=tuple(data.get("items") or ()), ordered=bool(data.get("ordered")))
    if kind == "system":
        return SystemBlock(
            text=str(data.get("text") or ""),
            level=str(data.get("level") or "info"),
            code=str(data.get("code") or ""),
        )
    spans = data.get("spans")
    cls = Paragraph if kind == "paragraph" else Line
    return cls(
        text=str(data.get("text") or ""),
        style=str(data.get("style") or ""),
        spans=None if spans is None else tuple(span_from_dict(span) for span in spans),
    )


def _resolve_span(span, ctx, resolver, collected):
    """Resolve one span, recording the (entity, resolved label) it named."""
    from evennia.narrative.render import render_spans

    value = render_spans([span], ctx, resolver)
    entity_id, kind, role = _ref_identity(span)
    if entity_id is not None and value:
        collected.append((entity_id, kind, role, value))
    return value


def _resolve_blocks(blocks, ctx, resolver, collected):
    resolved = []
    for block in blocks:
        if isinstance(block, Section):
            resolved.append(
                replace(
                    block,
                    children=tuple(_resolve_blocks(block.children, ctx, resolver, collected)),
                )
            )
            continue
        spans = getattr(block, "spans", None)
        if spans is None:
            resolved.append(block)
            continue
        text = "".join(_resolve_span(span, ctx, resolver, collected) for span in spans)
        resolved.append(replace(block, text=text, spans=None))
    return resolved


def resolve(
    plan: RenderPlan,
    viewer,
    *,
    resolver=None,
    with_refs: bool = True,
    extras: Mapping[str, Any] | None = None,
) -> RenderNode:
    """Resolve a canonical plan into one viewer's delivered node.

    This is the single point at which references become text. Everything a
    viewer receives -- the flattened ``body`` a telnet client prints, the block
    tree a shell renders, the handles it hangs interactivity on -- is derived
    here from one resolution, so they cannot disagree.

    Args:
        plan (RenderPlan): the canonical event.
        viewer: the perceiving object (``None`` for a neutral/camera render).
        resolver: override the installed :func:`get_span_resolver`.
        with_refs (bool): mint per-viewer entity handles. Skipped for text-only
            recipients, who never see them.
        extras (Mapping): extra :class:`ViewerContext` facts (distance,
            modality, lighting) -- how a relay re-resolves one event for a
            remote viewer without rebuilding it.

    Returns:
        RenderNode: the immutable delivered node.
    """
    resolver = resolver or get_span_resolver()
    # The canonical event itself is resolver context. Cross-reference passes
    # (an exit hallucination choosing another exit in the same scene, forensic
    # policy inspecting event metadata) can reason about the whole plan without
    # producers threading bespoke copies through ``extras``.
    default_extras = {"plan": plan}
    if plan.metadata.get("third_person"):
        default_extras["third_person"] = True
    ctx = ViewerContext(viewer=viewer, extras={**default_extras, **dict(extras or {})})
    collected = []
    blocks = tuple(_resolve_blocks(plan.blocks, ctx, resolver, collected))
    body = flatten_blocks(blocks, plan.sep)

    refs = ()
    from_handle = None
    if with_refs and viewer is not None:
        from evennia.narrative.handles import handle_for

        seen = {}
        built = []
        for entity_id, kind, role, label in collected:
            if (entity_id, label) in seen:
                continue
            seen[(entity_id, label)] = True
            entity = _entity(entity_id)
            if entity is None:
                continue
            handle = handle_for(viewer, entity, label)
            if entity_id == plan.subject_id and from_handle is None:
                from_handle = handle
            built.append(
                EntityRef(
                    handle=handle,
                    label=label,
                    kind=kind or "entity",
                    role=role or "target",
                )
            )
        refs = tuple(built)

    return RenderNode(
        kind=plan.kind,
        msg_type=plan.msg_type,
        body=body,
        from_handle=from_handle,
        refs=refs,
        blocks=blocks,
        sep=plan.sep,
        metadata=dict(plan.metadata),
        correlation_id=plan.correlation_id,
    )


def text_plan(text: str, *, kind: str = "text", msg_type: str = "text", **kwargs) -> RenderPlan:
    """Wrap plain output as a canonical plan.

    System output needs no semantic decomposition -- but it does need to travel
    the same delivery boundary, so sinks, timelines, and transforms see every
    message rather than only the decomposed ones.
    """
    return RenderPlan(
        kind=kind,
        msg_type=msg_type,
        blocks=(Line(spans=(TextSpan(str(text)),)),),
        **kwargs,
    )


def frame_plan(plan, prefix, *, suffix="", relay="", metadata=None):
    """Wrap a canonical plan in literal relay framing without resolving it.

    The source references and correlation id remain intact, so the framed
    perspective is another delivery of the same event rather than a new event.
    """

    def _prepend(block):
        if isinstance(block, Section):
            if not block.children:
                return block
            return replace(
                block,
                children=(_prepend(block.children[0]), *tuple(block.children[1:])),
            )
        if isinstance(block, (Line, Paragraph)):
            spans = block.spans
            if spans is None:
                return replace(block, text=str(prefix) + block.text)
            return replace(block, spans=(TextSpan(str(prefix)), *tuple(spans)))
        return Line(spans=(TextSpan(str(prefix)), TextSpan(str(getattr(block, "text", "")))))

    def _append(block):
        if not suffix:
            return block
        if isinstance(block, Section):
            if not block.children:
                return block
            return replace(
                block,
                children=(*tuple(block.children[:-1]), _append(block.children[-1])),
            )
        if isinstance(block, (Line, Paragraph)):
            spans = block.spans
            if spans is None:
                return replace(block, text=block.text + str(suffix))
            return replace(block, spans=(*tuple(spans), TextSpan(str(suffix))))
        return block

    blocks = tuple(plan.blocks)
    framed_blocks = list(
        (_prepend(blocks[0]), *blocks[1:]) if blocks else (Line(spans=(TextSpan(str(prefix)),)),)
    )
    framed_blocks[-1] = _append(framed_blocks[-1])
    return RenderPlan(
        kind=plan.kind,
        msg_type=plan.msg_type,
        blocks=tuple(framed_blocks),
        sep=plan.sep,
        subject_id=plan.subject_id,
        metadata={
            **dict(plan.metadata),
            **({"relay": relay} if relay else {}),
            **dict(metadata or {}),
        },
        correlation_id=plan.correlation_id,
    )


def deliver(
    plan: RenderPlan,
    viewer,
    *,
    from_obj=None,
    sessions=None,
    options=None,
    extras=None,
    publish=True,
    **msg_kwargs,
):
    """Resolve ``plan`` for ``viewer``, run universal transforms, and send.

    The one delivery service. Every registered perception, language, psychosis,
    accessibility, and presentation transform runs here, exactly once, for every
    client kind.
    """
    from evennia.narrative.rendernode import MODE_OFF, _sessions, narrative_mode
    from evennia.narrative.timeline import record_event

    if publish:
        record_event(plan)

    target = list(sessions) if sessions is not None else _sessions(viewer)
    wants_refs = any(narrative_mode(session) != MODE_OFF for session in target)
    node = resolve(plan, viewer, with_refs=wants_refs, extras=extras)
    # A game may attach a perspective relay (borrowed sight, remote sensorium)
    # to the perceiving object. Invoke it once at the plan boundary, before
    # protocol fan-out, so text/nodes/both clients cannot duplicate or bypass
    # the remote perception.
    if not msg_kwargs.get("_perception_relay"):
        relay_hook = getattr(viewer, "at_narrative_plan", None)
        if callable(relay_hook):
            try:
                relay_hook(plan, extras=extras)
            except Exception:
                from evennia.utils import logger

                logger.log_trace("narrative perspective relay failed")
    msg_kwargs.setdefault("_narrative_relayed", True)
    return deliver_resolved(
        node,
        viewer,
        from_obj=from_obj,
        sessions=target,
        options=options,
        context={"plan": plan},
        **msg_kwargs,
    )


def deliver_resolved(
    node: RenderNode,
    viewer,
    *,
    from_obj=None,
    sessions=None,
    options=None,
    context=None,
    **msg_kwargs,
):
    """Run universal transforms over an already-resolved node, then send it.

    The delivery boundary for surfaces that still resolve their own output. It
    exists so "not yet migrated to a plan" never means "skips the transforms" --
    a partially migrated game must not have perception or psychosis apply on
    some surfaces and not others. A transform may return the pipeline's explicit
    ``DROP_DELIVERY`` sentinel to suppress this viewer's delivery without
    emitting an empty structured message.
    """
    from evennia.narrative.pipeline import DROP_DELIVERY, transforms
    from evennia.narrative.rendernode import deliver_node

    ctx = {"from_obj": from_obj, **(context or {})}
    ctx["options"] = options
    if not ctx.get("hooks_applied"):
        if not _run_message_hooks(
            node,
            viewer,
            from_obj=from_obj,
            options=options,
            msg_kwargs=msg_kwargs,
        ):
            return None
        ctx["hooks_applied"] = True
    for _key, transform in transforms():
        node = transform(node, viewer, dict(ctx))
        if node is DROP_DELIVERY:
            return None
        if not isinstance(node, RenderNode):
            raise TypeError("render transforms must return RenderNode or DROP_DELIVERY")
    # A game may mirror what a viewer perceives to somewhere else: a puppeteer's
    # feed, a remote terminal, an observer window. This is the one place to do
    # it. Every route ends here — a canonical plan through :func:`deliver`, and
    # an already-resolved node from a surface that shapes its own output — and
    # it is still upstream of protocol fan-out, which sends a variable number of
    # times per event depending on what the client can take. A mirror hung off
    # ``msg`` instead fires twice for a client accepting both payloads and not
    # at all for a nodes-only one, whose call carries no text.
    if not msg_kwargs.get("_perception_relay"):
        mirror = getattr(viewer, "at_narrative_delivery", None)
        if callable(mirror):
            try:
                mirror(node, context=dict(ctx))
            except Exception:
                from evennia.utils import logger

                logger.log_trace("narrative delivery mirror failed")
    return deliver_node(
        node,
        viewer,
        from_obj=from_obj,
        sessions=sessions,
        options=options,
        _transformed=True,
        **msg_kwargs,
    )


def _run_message_hooks(node, viewer, *, from_obj=None, options=None, msg_kwargs=None):
    """Run the public send/receive hooks once at the delivery boundary."""
    from evennia.utils import logger
    from evennia.utils.utils import make_iter

    kwargs = dict(msg_kwargs or {})
    kwargs["options"] = options
    if from_obj:
        for sender in make_iter(from_obj):
            hook = getattr(sender, "at_msg_send", None)
            if not callable(hook):
                continue
            try:
                hook(text=node, to_obj=viewer, **kwargs)
            except Exception:
                logger.log_trace()
    hook = getattr(viewer, "at_msg_receive", None)
    if not callable(hook):
        return True
    try:
        return hook(text=node, from_obj=from_obj, **kwargs) is not False
    except Exception:
        logger.log_trace()
        return True


def deliver_to(
    plan: RenderPlan,
    viewers,
    *,
    from_obj=None,
    options=None,
    extras=None,
    publish=True,
    **msg_kwargs,
):
    """Record one canonical event, then resolve and deliver it to each viewer.

    The broadcast form of :func:`deliver`. The event reaches canonical sinks
    (recording, camera, forensics) exactly once, carrying its references; every
    viewer then gets their own resolution of that same event, tied together by
    the plan's ``correlation_id``.

    Args:
        plan (RenderPlan): the canonical event.
        viewers (iterable): recipients.
        from_obj: sender, passed through to ``msg`` hooks.
        options (dict): protocol options.
        extras (Mapping or callable): viewer-context facts. Pass a callable
            ``fn(viewer) -> dict`` to vary them per recipient (distance,
            modality, relay depth).
        publish (bool): publish the canonical event once. Perspective relays
            pass ``False`` because their source event was already published.
    """
    from evennia.narrative.timeline import record_event

    if publish:
        try:
            record_event(plan)
        except Exception:
            from evennia.utils import logger

            logger.log_trace("canonical event sink failed")
    for viewer in viewers:
        viewer_extras = extras(viewer) if callable(extras) else extras
        deliver(
            plan,
            viewer,
            from_obj=from_obj,
            options=options,
            extras=viewer_extras,
            publish=False,
            **msg_kwargs,
        )


# Re-exported for producers building plans; keeps one import line at call sites.
__all__ += [
    "TextSpan",
    "CharRef",
    "PronounRef",
    "SpeechSpan",
    "ObjectRef",
    "SelfRef",
    "SenderRef",
    "Line",
    "Paragraph",
    "Section",
    "ListBlock",
    "SystemBlock",
]
