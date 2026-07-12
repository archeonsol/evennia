"""
Default emote delivery (emote tiers B1/B3 substrate).

Uses :class:`~evennia.narrative.protocols.KeyNameResolver` unless a custom
:class:`~evennia.narrative.protocols.NameResolver` is passed in.
"""

from __future__ import annotations

import re

from .emote import (
    EmotePlan,
    EmoteResult,
    build_caller_echo,
    build_camera_text,
    build_emote_for_viewer,
    build_emote_segment_plans,
    format_emote_message,
    replace_first_pronoun_with_name,
    split_emote_segments,
)
from .protocols import KeyNameResolver, NameResolver
from .rendernode import EntityRef, RenderNode, deliver_node

__all__ = ["DefaultEmoteDelivery", "default_emote_delivery"]


def _viewer_refs(plan, viewer, resolver):
    """Per-viewer target references for a RenderNode: name-as-seen + a
    viewer-scoped handle (never a raw db id — see
    :mod:`evennia.narrative.handles`)."""
    from evennia.narrative.handles import handle_for

    refs = []
    seen = set()
    for sp in plan.segment_plans:
        for _matched, char in sp.targets:
            cid = getattr(char, "id", None)
            if cid in seen:
                continue
            seen.add(cid)
            name = resolver.display_name(char, viewer)
            refs.append(
                EntityRef(
                    handle=handle_for(viewer, char, name),
                    label=name,
                    role="target",
                )
            )
    return refs


class DefaultEmoteDelivery:
    """Engine-default :class:`EmoteDelivery` using key-based targeting."""

    def __init__(self, resolver: NameResolver | None = None):
        self.resolver = resolver or KeyNameResolver()

    def _room_characters(self, location):
        if location is None:
            return []
        return list(location.contents_get(content_type="character"))

    def build_plan(self, caller, text: str, *, msg_type: str = "pose", improvise: bool = False):
        location = getattr(caller, "location", None)
        if not location:
            return None

        segments = split_emote_segments(text)
        starts_comma = bool(segments and segments[0].strip().startswith(","))
        pronoun_key = getattr(getattr(caller, "db", None), "pronoun", None) or "neutral"
        char_list = self._room_characters(location)
        segment_plans = build_emote_segment_plans(
            segments, caller, char_list, resolver=self.resolver
        )
        caller_echo = build_caller_echo(
            segments, starts_comma, segment_plans, caller, resolver=self.resolver
        )
        camera_text = build_camera_text(
            segment_plans, starts_comma, pronoun_key, caller, resolver=self.resolver
        )
        viewers = [c for c in char_list if c != caller] + [caller]

        return EmotePlan(
            segment_plans=segment_plans,
            caller_echo=caller_echo,
            camera_text=camera_text,
            starts_comma=starts_comma,
            pronoun_key=pronoun_key,
            lang_key="",
            msg_type=msg_type,
            improvise=improvise,
            caller=caller,
            location=location,
            viewers=viewers,
        )

    def deliver(self, plan: EmotePlan) -> EmoteResult:
        caller = plan.caller
        all_targets = [t for sp in plan.segment_plans for t in sp.targets]
        delivered_to = []

        def _build_viewer_body(viewer):
            body_parts = []
            for sp in plan.segment_plans:
                body_part = build_emote_for_viewer(
                    sp.third_text, viewer, sp.targets, resolver=self.resolver
                )
                for ph, quote_text in sp.lang_bits:
                    body_part = body_part.replace(ph, f'"{quote_text}"')
                body_parts.append(body_part)
            full_body = ". ".join(p.strip() for p in body_parts if p.strip()).strip()
            if full_body and not full_body.endswith((".", "!", "?", '"')):
                full_body += "."
            return re.sub(r"\.\s+(\w)", lambda m: ". " + m.group(1).upper(), full_body)

        for viewer in plan.viewers:
            if viewer == caller:
                msg = plan.caller_echo
            else:
                full_body = _build_viewer_body(viewer)
                if plan.starts_comma:
                    full_body = replace_first_pronoun_with_name(
                        full_body,
                        plan.pronoun_key,
                        caller,
                        viewer,
                        resolver=self.resolver,
                    )
                    msg = full_body
                else:
                    msg = format_emote_message(caller, viewer, full_body, resolver=self.resolver)
            if plan.improvise:
                msg = "|w%s|n" % msg
            if hasattr(viewer, "msg"):
                # R1 seam: wrap the per-viewer string in a RenderNode and let
                # deliver_node flatten it (text parity) or send it structured
                # (W1) per client capability.
                from evennia.narrative.handles import handle_for

                caller_name = self.resolver.display_name(caller, viewer)
                node = RenderNode(
                    kind="emote",
                    msg_type=plan.msg_type,
                    body=msg,
                    from_handle=handle_for(viewer, caller, caller_name),
                    self_echo=(viewer == caller),
                )
                # refs carry the per-viewer resolver output; build them only
                # when deliver_node finds a capable session (telnet viewers, the
                # common case, discard them).
                deliver_node(
                    node,
                    viewer,
                    from_obj=caller,
                    refs_builder=lambda v=viewer: _viewer_refs(plan, v, self.resolver),
                )
            if viewer != caller:
                delivered_to.append(viewer)

        return EmoteResult(
            targets=all_targets,
            delivered_to=delivered_to,
            camera_text=plan.camera_text,
            caller_echo=plan.caller_echo,
        )

    def run(
        self,
        caller,
        text: str,
        *,
        msg_type: str = "pose",
        improvise: bool = False,
        literal_third: bool = False,
    ):
        text = (text or "").strip()
        if not text or not text.lstrip(".,").strip():
            caller.msg("Usage: . <first-person text>")
            return None

        location = getattr(caller, "location", None)
        if not location:
            return None

        if literal_third:
            char_list = self._room_characters(location)
            viewers = [c for c in char_list if c != caller] + [caller]
            body = text.rstrip(".")
            if body and not text.endswith((".", "!", "?")):
                body = body + "."
            elif text.endswith((".", "!", "?")):
                body = text
            for viewer in viewers:
                if viewer == caller:
                    plain = self.resolver.display_name(caller, caller)
                    msg = f"{plain} {body}".strip()
                else:
                    msg = format_emote_message(caller, viewer, body, resolver=self.resolver)
                if improvise:
                    msg = "|w%s|n" % msg
                if hasattr(viewer, "msg"):
                    from evennia.narrative.handles import handle_for

                    caller_name = self.resolver.display_name(caller, viewer)
                    deliver_node(
                        RenderNode(
                            kind="emote",
                            msg_type=msg_type,
                            body=msg,
                            from_handle=handle_for(viewer, caller, caller_name),
                            refs=(
                                EntityRef(
                                    handle=handle_for(viewer, caller, caller_name),
                                    label=caller_name,
                                    role="emitter",
                                ),
                            ),
                            self_echo=viewer == caller,
                            metadata={"literal_third": True, "improvise": bool(improvise)},
                        ),
                        viewer,
                        from_obj=caller,
                    )
            return None

        plan = self.build_plan(caller, text, msg_type=msg_type, improvise=improvise)
        if plan is None:
            return None
        return self.deliver(plan)


default_emote_delivery = DefaultEmoteDelivery()
