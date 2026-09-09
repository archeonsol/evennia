"""Accepted narrative fields survive resolution and serialization unchanged."""

import json
from unittest import TestCase
from unittest.mock import patch

from evennia.narrative.plan import RenderPlan, resolve
from evennia.narrative.render import (
    CharRef,
    KeyResolver,
    ObjectRef,
    PronounRef,
    SelfRef,
    SenderRef,
    SpeechSpan,
    TextSpan,
)
from evennia.narrative.rendernode import (
    MAX_BLOCKS,
    MAX_BODY_CHARS,
    EntityRef,
    Line,
    ListBlock,
    Paragraph,
    RenderNode,
    Section,
    SystemBlock,
)


class TestRenderLimits(TestCase):
    """Construction owns content limits for both direct and resolved output."""

    def test_display_fields_preserve_text_at_boundaries(self):
        """Every text representation retains the same final character."""
        for size in (4096, 4097, MAX_BODY_CHARS):
            text = "漢" * (size - 1) + "🙂"
            for block, field in (
                (Line(text), "text"),
                (Paragraph(text), "text"),
                (Section("s", title=text), "title"),
                (ListBlock((text,)), "items"),
                (SystemBlock(text), "text"),
            ):
                with self.subTest(size=size, block=type(block).__name__):
                    direct = RenderNode(kind="test", msg_type="text", body=text, blocks=(block,))
                    resolved = resolve(
                        RenderPlan(kind="test", blocks=(block,)), None, with_refs=False
                    )
                    for node in (direct, resolved):
                        payload = json.loads(json.dumps(node.payload()))
                        value = payload["blocks"][0][field]
                        self.assertEqual(value[0] if field == "items" else value, text)
                        self.assertEqual(payload["body"], text)
            ref = EntityRef(handle="h", label=text)
            self.assertEqual(ref.payload()["label"], text)
            self.assertEqual(ref.payload()["name"], text)

    def test_display_fields_reject_excess_at_construction(self):
        """No block can silently accept text that its payload cannot retain."""
        text = "x" * (MAX_BODY_CHARS + 1)
        for make in (
            lambda: Line(text),
            lambda: Paragraph(text),
            lambda: Section("s", title=text),
            lambda: ListBlock((text,)),
            lambda: SystemBlock(text),
            lambda: EntityRef(handle="h", label=text),
        ):
            with self.subTest(make=make), self.assertRaises(ValueError):
                make()

    def test_machine_fields_reject_excess_instead_of_clipping(self):
        """Identifiers cannot change between their producer and consumer."""
        for constructor, base, fields in (
            (EntityRef, {"handle": "h", "label": "name"}, {"handle": 128, "kind": 64, "role": 64}),
            (Line, {}, {"style": 64}),
            (Paragraph, {}, {"style": 64}),
            (Section, {"key": "s"}, {"key": 64, "style": 64, "sep": 4096}),
            (ListBlock, {"items": ()}, {"style": 64}),
            (SystemBlock, {"text": "x"}, {"level": 32, "code": 128}),
            (
                RenderNode,
                {"kind": "test", "msg_type": "text", "body": "x"},
                {
                    "kind": 64,
                    "msg_type": 64,
                    "schema": 64,
                    "node_id": 128,
                    "correlation_id": 128,
                    "from_handle": 128,
                    "sep": 4096,
                },
            ),
        ):
            for name, size in fields.items():
                with self.subTest(record=constructor.__name__, field=name):
                    value = "x" * size
                    record = constructor(**{**base, name: value})
                    self.assertEqual(record.payload()[name], value)
                    with self.assertRaises(ValueError):
                        constructor(**{**base, name: value + "x"})
        for affordances in (("x" * 65,), tuple("x" for _ in range(33))):
            with self.assertRaises(ValueError):
                EntityRef(handle="h", label="name", affordances=affordances)

    def test_canonical_identifiers_are_validated_before_resolution(self):
        """Canonical identifiers keep the same limits when copied to a node."""
        for name, limit in (
            ("kind", 64),
            ("msg_type", 64),
            ("plan_id", 128),
            ("correlation_id", 128),
            ("sep", 4096),
        ):
            with self.subTest(field=name), self.assertRaises(ValueError):
                RenderPlan(**{"kind": "test", name: "x" * (limit + 1)})

    def test_non_json_metadata_is_rejected_at_construction(self):
        """Metadata remains portable to strict JSON readers."""
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RenderPlan(kind="test", metadata={"value": value})
        with self.assertRaises(TypeError):
            RenderPlan(kind="test", metadata={1: "value"})

    def test_text_fields_require_strings(self):
        """Type errors are exposed at the field owner."""
        for make in (
            lambda: Line(123),
            lambda: ListBlock((123,)),
            lambda: EntityRef(handle="h", label=123),
            lambda: RenderPlan(kind=123),
        ):
            with self.assertRaises(TypeError):
                make()

    def test_metadata_rejects_clipping_and_key_collisions(self):
        """Metadata validation runs before either record is accepted."""
        for metadata in (
            {"k": "x" * (MAX_BODY_CHARS + 1)},
            {"x" * 128 + "a": 1, "x" * 128 + "b": 2},
        ):
            for constructor, args in (
                (RenderPlan, {"kind": "test"}),
                (RenderNode, {"kind": "test", "msg_type": "text", "body": "x"}),
            ):
                with self.subTest(record=constructor.__name__), self.assertRaises(ValueError):
                    constructor(metadata=metadata, **args)

    def test_metadata_depth_is_relative_to_metadata(self):
        """A wire wrapper consumes none of the metadata's own depth budget."""
        metadata = "value"
        for _ in range(8):
            metadata = {"key": metadata}
        node = RenderNode(kind="test", msg_type="text", body="x", metadata=metadata)
        self.assertEqual(node.payload()["metadata"], metadata)
        with self.assertRaises(ValueError):
            RenderPlan(kind="test", metadata={"extra": metadata})

    def test_accepted_block_depth_survives_json(self):
        """The block count bounds nesting independently of metadata depth."""
        block = Line("x")
        for _ in range(MAX_BLOCKS - 1):
            block = Section("s", children=(block,))
        plan = RenderPlan(kind="test", blocks=(block,))
        node = resolve(plan, None, with_refs=False)
        payload = json.loads(json.dumps(node.payload()))
        leaf = payload["blocks"][0]
        for _ in range(MAX_BLOCKS - 1):
            leaf = leaf["children"][0]
        self.assertEqual(leaf["text"], "x")

    def test_resolved_span_text_is_validated(self):
        """Viewer expansion must fit even when the authored fallback is empty."""
        plan = RenderPlan(kind="test", blocks=(Line(spans=(CharRef(1),)),))
        with (
            patch.object(KeyResolver, "char", return_value="x" * (MAX_BODY_CHARS + 1)),
            self.assertRaises(ValueError),
        ):
            resolve(plan, None, resolver=KeyResolver(), with_refs=False)

    def test_authored_span_fields_are_validated_before_delivery(self):
        """Canonical and direct spans cannot hide oversized display text."""
        span = TextSpan("x" * (MAX_BODY_CHARS + 1))
        with self.assertRaises(ValueError):
            RenderPlan(kind="test", blocks=(Line(spans=(span,)),))
        with self.assertRaises(ValueError):
            RenderNode(kind="test", msg_type="text", body="x", spans=((span,),))

    def test_span_machine_fields_have_their_own_limits(self):
        """Span tags share identifier limits across canonical and direct nodes."""
        for size in (64, 65):
            tag = "x" * size
            for span in (
                CharRef(1, role=tag),
                ObjectRef(1, kind=tag),
                ObjectRef(1, role=tag),
                PronounRef(1, form=tag, original="they"),
                SelfRef(form=tag),
                SpeechSpan("hello", lang=tag),
                SenderRef(1, alias="sender", channel=tag),
            ):
                with self.subTest(span=span):
                    if size == 64:
                        RenderPlan(kind="test", blocks=(Line(spans=(span,)),))
                        RenderNode(kind="test", msg_type="text", body="x", spans=((span,),))
                    else:
                        with self.assertRaises(ValueError):
                            RenderPlan(kind="test", blocks=(Line(spans=(span,)),))
                        with self.assertRaises(ValueError):
                            RenderNode(kind="test", msg_type="text", body="x", spans=((span,),))

    def test_direct_span_collections_are_bounded_at_construction(self):
        """The public span sidecar retains every accepted segment and item."""
        for spans in (((TextSpan("x"),) * 257,), ((TextSpan("x"),),) * 257):
            with self.assertRaises(ValueError):
                RenderNode(kind="test", msg_type="text", body="x", spans=spans)

    def test_combined_text_budget_is_checked_after_resolution(self):
        """Individually valid fields must still fit the complete body."""
        blocks = (Line("x" * (MAX_BODY_CHARS // 2)), Line("x" * (MAX_BODY_CHARS // 2)))
        plan = RenderPlan(kind="test", blocks=blocks)
        with self.assertRaises(ValueError):
            resolve(plan, None, with_refs=False)
