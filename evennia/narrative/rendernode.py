"""RenderNode v0 and capability-gated delivery (R1 first slice).

This is the first real R1 seam beyond the emote *plan*: the point where a
per-viewer emote stops being a structured thing and becomes a string. Instead of
flattening straight into ``viewer.msg``, an emitter builds a :class:`RenderNode`
and hands it to :func:`deliver_node`, which either:

- flattens it to the **same** string and sends it via ``viewer.msg`` (telnet and
  any client that has not announced structured-render support), or
- sends it as a structured ``narrative`` OOB payload to a session whose client
  announced support (the W1 path).

``RenderNode`` v0 is intentionally minimal: it *wraps* the already-flattened
``body`` string (so the telnet path is byte-identical) and carries the per-viewer
target ``refs`` that a rich client wants and plain text cannot express. Splitting
``body`` into typed inline spans is a later slice; see
``.agents/docs/engine-architecture/r1-first-slice.md``.

The string API (``msg``/``return_appearance``) is untouched. This module is
additive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["RenderNode", "deliver_node", "CLIENT_NARRATIVE_FLAG"]

# Protocol flag a client sets (via the ``narrative_client`` inputfunc) to declare
# it renders structured narrative nodes rather than plain text lines.
CLIENT_NARRATIVE_FLAG = "CLIENT_NARRATIVE"


@dataclass
class RenderNode:
    """A minimal, per-viewer unit of structured output.

    Args:
        kind (str): node family, e.g. ``"emote"``.
        msg_type (str): the message type (``"pose"``, ``"say"``, ...), preserved
            for both the text ``type`` metadata and structured styling.
        body (str): the fully flattened, per-viewer string. The text path sends
            this verbatim, so it is the parity anchor.
        from_id (int | None): dbref id of the emitter (speaker).
        refs (list[dict]): per-viewer target references, each
            ``{"name": <name this viewer saw>, "char_id": <dbref id>}``.
        self_echo (bool): whether this node is the emitter's own echo.
    """

    kind: str
    msg_type: str
    body: str
    from_id: int | None = None
    refs: list = field(default_factory=list)
    self_echo: bool = False
    # Optional viewer-invariant span tree(s) behind ``body``. Present once a
    # surface has been decomposed into spans (a list of per-segment span lists).
    # ``body`` stays the authoritative flattened text; ``spans`` is the
    # structure a rich client renders and a store keeps for later re-resolution.
    spans: list | None = None

    def payload(self) -> dict:
        """JSON-friendly payload for the structured OOB path (client contract).

        Keys are snake_case to match the engine's other OOB surfaces (the
        ``editor_*`` kwargs and the nested ``refs`` ``char_id``), so a client
        sees one casing convention across all narrative/editor payloads.
        """
        data = {
            "kind": self.kind,
            "msg_type": self.msg_type,
            "body": self.body,
            "from_id": self.from_id,
            "refs": self.refs,
            "self_echo": self.self_echo,
        }
        if self.spans is not None:
            from evennia.narrative.render import span_to_dict

            data["spans"] = [[span_to_dict(s) for s in seg] for seg in self.spans]
        return data


def _capable_session(viewer):
    """Return a viewer session that announced structured-render support, or None."""
    handler = getattr(viewer, "sessions", None)
    if handler is None:
        return None
    try:
        sessions = list(handler.all())
    except Exception:
        # The no-handler case is already handled above, so this only fires on a
        # genuine session-handler fault (e.g. a DB error during _recache). Log it
        # rather than bury it, then degrade to the text path for this viewer.
        from evennia.utils import logger

        logger.log_trace()
        return None
    for sess in sessions:
        flags = getattr(sess, "protocol_flags", None) or {}
        if flags.get(CLIENT_NARRATIVE_FLAG):
            return sess
    return None


def deliver_node(node: RenderNode, viewer, from_obj=None, refs_builder=None):
    """Deliver ``node`` to ``viewer``, structured or flattened per capability.

    When no session announced support, this is byte-identical to the legacy
    ``viewer.msg((body, {"type": msg_type}), from_obj=from_obj)`` call, so it is a
    drop-in for an emote delivery loop's final send.

    Args:
        node (RenderNode): the per-viewer node to deliver.
        viewer: the recipient (must expose ``msg``).
        from_obj: the emitter, passed through to ``msg`` as ``from_obj``.
        refs_builder (callable, optional): a zero-arg callable returning the
            per-viewer ``refs`` list. Called only when a capable session is
            found and ``node.refs`` is empty, so an expensive per-viewer resolver
            is not run for the common text-only (telnet) viewer.
    """
    text_msg = (node.body, {"type": node.msg_type})
    cap = _capable_session(viewer)
    if cap is None:
        # Unchanged text path — the parity anchor. No refs built here.
        viewer.msg(text_msg, from_obj=from_obj)
        return
    if refs_builder is not None and not node.refs:
        node.refs = refs_builder()
    # Structured to the capable session; text to any other (e.g. telnet) sessions
    # the same viewer has open, so a mixed-client player loses nothing.
    viewer.msg(narrative=([node.payload()], {}), session=cap, from_obj=from_obj)
    try:
        others = [s for s in viewer.sessions.all() if s is not cap]
    except Exception:
        # cap was just resolved from the same handler, so a fault here is
        # genuine; log it before degrading to no extra text sends.
        from evennia.utils import logger

        logger.log_trace()
        others = []
    if others:
        viewer.msg(text_msg, session=others, from_obj=from_obj)
