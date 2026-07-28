"""Unit tests for the canonical RenderPlan -> resolved RenderNode seam.

Pure: fake viewers/sessions/resolvers, no DB. Pins the contracts the rest of R1
rests on -- one canonical event resolves differently per viewer, body is derived
from the same resolution the blocks are, and text-only clients travel the same
path as structured ones.
"""

import unittest
from dataclasses import FrozenInstanceError

from evennia.narrative import plan as plan_mod
from evennia.narrative.plan import (
    CharRef,
    Line,
    Paragraph,
    RenderPlan,
    Section,
    SelfRef,
    SenderRef,
    TextSpan,
    deliver,
    deliver_to,
    resolve,
    text_plan,
)
from evennia.narrative.render import ExitRef, ItemRef, KeyResolver, PipelineResolver
from evennia.narrative.rendernode import (
    CLIENT_NARRATIVE_FLAG,
    MODE_BOTH,
    MODE_NODES,
    MODE_OFF,
    flatten_blocks,
    narrative_mode,
)


class _Entity:
    def __init__(self, entity_id, key):
        self.id = entity_id
        self.key = key


class _Ndb:
    pass


class _Sessions:
    def __init__(self, sessions):
        self._s = sessions

    def all(self):
        return list(self._s)


class _Session:
    def __init__(self, flags=None):
        self.protocol_flags = flags or {}


class _Viewer:
    def __init__(self, key="Viewer", sessions=None, knows=()):
        self.id = abs(hash(key)) % 100000
        self.key = key
        self.ndb = _Ndb()
        self.knows = set(knows)
        self.calls = []
        self.sessions = _Sessions(sessions or [])

    def msg(self, *args, **kwargs):
        self.calls.append((args, kwargs))


KADE = _Entity(1, "Kade")
NORTH = _Entity(2, "north")
KNIFE = _Entity(3, "a knife")
_WORLD = {1: KADE, 2: NORTH, 3: KNIFE}


class _RecogResolver(KeyResolver):
    """Names a character only if the viewer recognizes them."""

    def char(self, ref, ctx):
        entity = _WORLD.get(ref.char_id)
        viewer = ctx.viewer
        if viewer is not None and ref.char_id in getattr(viewer, "knows", ()):
            return entity.key
        return "someone"


class PlanTestCase(unittest.TestCase):
    def setUp(self):
        plan_mod.set_entity_lookup(_WORLD.get)
        plan_mod.set_span_resolver(_RecogResolver())
        self.addCleanup(plan_mod.set_entity_lookup, None)
        self.addCleanup(plan_mod.set_span_resolver, None)


class TestResolution(PlanTestCase):
    def _plan(self):
        return RenderPlan(
            kind="emote",
            msg_type="pose",
            subject_id=1,
            blocks=(
                Line(
                    spans=(
                        CharRef(char_id=1, role="emitter"),
                        TextSpan(" leans against the wall."),
                    )
                ),
            ),
        )

    def test_one_plan_resolves_differently_per_viewer(self):
        plan = self._plan()
        knower = _Viewer("Ana", knows=[1])
        stranger = _Viewer("Bo")
        self.assertEqual(resolve(plan, knower).body, "Kade leans against the wall.")
        self.assertEqual(resolve(plan, stranger).body, "someone leans against the wall.")

    def test_every_viewer_shares_one_correlation_id(self):
        plan = self._plan()
        a = resolve(plan, _Viewer("Ana", knows=[1]))
        b = resolve(plan, _Viewer("Bo"))
        self.assertTrue(plan.correlation_id)
        self.assertEqual(a.correlation_id, plan.correlation_id)
        self.assertEqual(b.correlation_id, plan.correlation_id)
        self.assertNotEqual(a.node_id, b.node_id)

    def test_body_and_blocks_come_from_the_same_resolution(self):
        node = resolve(self._plan(), _Viewer("Ana", knows=[1]))
        self.assertEqual(node.blocks[0].text, "Kade leans against the wall.")
        self.assertEqual(node.body, node.blocks[0].text)
        # resolved blocks no longer carry unresolved structure
        self.assertIsNone(node.blocks[0].spans)

    def test_refs_are_viewer_scoped_and_labelled_as_perceived(self):
        knower = _Viewer("Ana", knows=[1])
        stranger = _Viewer("Bo")
        known = resolve(self._plan(), knower)
        unknown = resolve(self._plan(), stranger)
        self.assertEqual(known.refs[0].label, "Kade")
        self.assertEqual(unknown.refs[0].label, "someone")
        # opaque, per-viewer, and never a database id
        self.assertNotEqual(known.refs[0].handle, unknown.refs[0].handle)
        self.assertNotIn("char_id", str(known.payload()))

    def test_refs_skipped_when_not_requested(self):
        node = resolve(self._plan(), _Viewer("Ana", knows=[1]), with_refs=False)
        self.assertEqual(node.refs, ())
        self.assertIsNone(node.from_handle)
        self.assertEqual(node.body, "Kade leans against the wall.")

    def test_plan_has_no_body_to_bake_a_name_into(self):
        self.assertFalse(hasattr(self._plan(), "body"))

    def test_extras_reach_the_resolver_context(self):
        seen = {}

        class _Ctx(KeyResolver):
            def char(self, ref, ctx):
                seen.update(ctx.extras)
                return "x"

        plan = self._plan()
        resolve(plan, _Viewer("Ana"), resolver=_Ctx(), extras={"distance": "far"})
        self.assertEqual(seen["distance"], "far")
        self.assertIs(seen["plan"], plan)

    def test_plan_is_deeply_immutable(self):
        metadata = {"surface": "test", "nested": {"values": [1, 2]}}
        span = TextSpan("before")
        plan = RenderPlan(
            kind="test",
            blocks=(Line(spans=(span,)),),
            metadata=metadata,
        )
        metadata["surface"] = "changed"
        metadata["nested"]["values"].append(3)
        self.assertEqual(plan.metadata["surface"], "test")
        self.assertEqual(plan.metadata["nested"]["values"], (1, 2))
        with self.assertRaises(TypeError):
            plan.metadata["surface"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            span.text = "after"

    def test_plan_rejects_non_primitive_metadata(self):
        with self.assertRaises(TypeError):
            RenderPlan(kind="test", metadata={"entity": KADE})


class TestNewReferenceKinds(PlanTestCase):
    def test_exit_item_self_and_sender_refs_resolve(self):
        plan = RenderPlan(
            kind="attack",
            blocks=(
                Line(
                    spans=(
                        SelfRef(form="subject", capitalize=True),
                        TextSpan(" swing "),
                        ItemRef(3),
                        TextSpan(" toward "),
                        ExitRef(2),
                        TextSpan("."),
                    )
                ),
            ),
        )
        self.assertEqual(resolve(plan, _Viewer("Ana")).body, "You swing a knife toward north.")

    def test_sender_ref_resolves_through_the_resolver(self):
        class _Alias(KeyResolver):
            def sender(self, ref, ctx):
                return ref.alias.upper()

        plan = RenderPlan(
            kind="network",
            blocks=(Line(spans=(SenderRef(sender_id=1, alias="ghost"), TextSpan(": hello"))),),
        )
        self.assertEqual(resolve(plan, _Viewer("Ana"), resolver=_Alias()).body, "GHOST: hello")

    def test_unperceivable_object_uses_its_fallback(self):
        plan = RenderPlan(kind="look", blocks=(Line(spans=(ExitRef(999),)),))
        self.assertEqual(resolve(plan, _Viewer("Ana")).body, "somewhere")

    def test_resolver_predating_a_ref_kind_still_renders_it(self):
        # A game resolver written before ObjectRef existed must not raise.
        class _Old:
            def char(self, ref, ctx):
                return "c"

            def pron(self, ref, ctx):
                return "p"

            def speech(self, ref, ctx):
                return "s"

        plan = RenderPlan(kind="look", blocks=(Line(spans=(ItemRef(3),)),))
        self.assertEqual(resolve(plan, _Viewer("Ana"), resolver=_Old()).body, "a knife")

    def test_pipeline_resolver_folds_passes_over_object_refs(self):
        class _Shout:
            def obj(self, value, ref, ctx):
                return value.upper()

        resolver = PipelineResolver().add_pass(_Shout())
        plan_mod.set_entity_lookup(_WORLD.get)
        plan = RenderPlan(kind="look", blocks=(Line(spans=(ItemRef(3),)),))
        # PipelineResolver's default obj namer goes through KeyResolver, whose
        # lookup is the engine one; give it the world explicitly.
        resolver.set_obj_namer(lambda ref, ctx: _WORLD[ref.object_id].key)
        self.assertEqual(resolve(plan, _Viewer("Ana"), resolver=resolver).body, "A KNIFE")


class TestFlatten(unittest.TestCase):
    def test_nested_sections_use_their_own_separator(self):
        blocks = (
            Section(
                key="head",
                children=(Line("Atrium"), Line("A large room.")),
                sep="\n",
            ),
            Section(key="exits", children=(Line("Exits: north"),), sep="\n"),
        )
        self.assertEqual(
            flatten_blocks(blocks, sep="\n\n"),
            "Atrium\nA large room.\n\nExits: north",
        )

    def test_empty_blocks_vanish_rather_than_leaving_gaps(self):
        blocks = (Line("one"), Line(""), Section(key="empty", children=()), Line("two"))
        self.assertEqual(flatten_blocks(blocks, sep="\n"), "one\ntwo")

    def test_flatten_emits_markup_not_ansi(self):
        # The portal owns markup -> ANSI/HTML; flatten must not pre-convert.
        self.assertEqual(flatten_blocks((Line("|rred|n"),)), "|rred|n")


class TestCapabilityTiers(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(narrative_mode(_Session({})), MODE_OFF)
        self.assertEqual(narrative_mode(_Session({CLIENT_NARRATIVE_FLAG: True})), MODE_NODES)
        self.assertEqual(narrative_mode(_Session({CLIENT_NARRATIVE_FLAG: "both"})), MODE_BOTH)
        self.assertEqual(narrative_mode(_Session({CLIENT_NARRATIVE_FLAG: "off"})), MODE_OFF)
        self.assertEqual(
            narrative_mode(_Session({"AZABAN_CAPS": {"rendersNodes": True}})), MODE_NODES
        )

    def test_unknown_mode_string_degrades_to_text(self):
        self.assertEqual(narrative_mode(_Session({CLIENT_NARRATIVE_FLAG: "wat"})), MODE_OFF)

    def test_msdp_only_session_cannot_carry_nodes(self):
        # MSDP's flat encoding would mangle a node tree: degrade to text.
        session = _Session({CLIENT_NARRATIVE_FLAG: True, "OOB_MSDP": True})
        self.assertEqual(narrative_mode(session), MODE_OFF)

    def test_gmcp_session_carries_nodes(self):
        session = _Session({CLIENT_NARRATIVE_FLAG: True, "OOB_MSDP": True, "OOB_GMCP": True})
        self.assertEqual(narrative_mode(session), MODE_NODES)


class TestDelivery(PlanTestCase):
    def _plan(self):
        return RenderPlan(
            kind="say",
            msg_type="say",
            blocks=(Line(spans=(CharRef(char_id=1, role="emitter"), TextSpan(' says, "hi"'))),),
        )

    def test_text_only_session_gets_the_flattened_body(self):
        viewer = _Viewer("Ana", sessions=[_Session({})], knows=[1])
        deliver(self._plan(), viewer)
        args, kwargs = viewer.calls[0]
        self.assertEqual(args[0], ('Kade says, "hi"', {"type": "say"}))
        self.assertNotIn("narrative", kwargs)

    def test_both_tier_gets_text_and_structure(self):
        session = _Session({CLIENT_NARRATIVE_FLAG: "both"})
        viewer = _Viewer("Ana", sessions=[session], knows=[1])
        deliver(self._plan(), viewer)
        self.assertEqual(len(viewer.calls), 2)
        self.assertIn("narrative", viewer.calls[0][1])
        self.assertEqual(viewer.calls[1][0][0], ('Kade says, "hi"', {"type": "say"}))

    def test_nodes_tier_gets_structure_only(self):
        session = _Session({CLIENT_NARRATIVE_FLAG: "nodes"})
        viewer = _Viewer("Ana", sessions=[session], knows=[1])
        deliver(self._plan(), viewer)
        self.assertEqual(len(viewer.calls), 1)
        self.assertIn("narrative", viewer.calls[0][1])

    def test_structured_and_text_clients_agree_on_the_line(self):
        plan = self._plan()
        rich = _Viewer("Ana", sessions=[_Session({CLIENT_NARRATIVE_FLAG: True})], knows=[1])
        telnet = _Viewer("Ana2", sessions=[_Session({})], knows=[1])
        deliver(plan, rich)
        deliver(plan, telnet)
        payload = rich.calls[0][1]["narrative"][0][0]
        self.assertEqual(payload["body"], telnet.calls[0][0][0][0])

    def test_deliver_to_records_the_canonical_event_once(self):
        from evennia.narrative import timeline

        seen = []

        class _Sink:
            def accept_event(self, plan):
                seen.append(plan)

        timeline.register_canonical_sink("test-canonical", _Sink(), override=True)
        self.addCleanup(timeline.unregister_canonical_sink, "test-canonical")
        plan = self._plan()
        viewers = [
            _Viewer("Ana", sessions=[_Session({})], knows=[1]),
            _Viewer("Bo", sessions=[_Session({})]),
        ]
        deliver_to(plan, viewers)
        self.assertEqual(seen, [plan])
        self.assertEqual(viewers[0].calls[0][0][0][0], 'Kade says, "hi"')
        self.assertEqual(viewers[1].calls[0][0][0][0], 'someone says, "hi"')

    def test_transforms_run_once_per_delivery(self):
        from evennia.narrative import pipeline

        pipeline.register_transform(
            "test-suffix",
            lambda node, viewer, ctx: node.map_text(lambda text: text + " [t]"),
            override=True,
        )
        self.addCleanup(pipeline.unregister_transform, "test-suffix")
        viewer = _Viewer("Ana", sessions=[_Session({})], knows=[1])
        deliver(self._plan(), viewer)
        self.assertEqual(viewer.calls[0][0][0][0], 'Kade says, "hi" [t]')

    def test_transform_keeps_structured_blocks_and_text_body_identical(self):
        from evennia.narrative import pipeline

        pipeline.register_transform(
            "test-distortion",
            lambda node, viewer, ctx: node.map_text(
                lambda text: text.replace("Kade", "someone else")
            ),
            override=True,
        )
        self.addCleanup(pipeline.unregister_transform, "test-distortion")
        viewer = _Viewer(
            "Ana",
            sessions=[_Session({CLIENT_NARRATIVE_FLAG: "both"})],
            knows=[1],
        )
        deliver(self._plan(), viewer)
        payload = viewer.calls[0][1]["narrative"][0][0]
        text_body = viewer.calls[1][0][0][0]
        self.assertEqual(payload["body"], text_body)
        self.assertEqual(payload["blocks"][0]["text"], text_body)

    def test_single_viewer_delivery_publishes_the_event(self):
        from evennia.narrative import timeline

        seen = []

        class _Sink:
            def accept_event(self, plan):
                seen.append(plan)

        timeline.register_canonical_sink("test-single", _Sink(), override=True)
        self.addCleanup(timeline.unregister_canonical_sink, "test-single")
        plan = self._plan()
        deliver(plan, _Viewer("Ana", sessions=[_Session({})], knows=[1]))
        self.assertEqual(seen, [plan])

    def test_repeated_perspective_delivery_publishes_one_event(self):
        from evennia.narrative import timeline

        seen = []

        class _Sink:
            def accept_event(self, plan):
                seen.append(plan)

        timeline.register_canonical_sink("test-repeat", _Sink(), override=True)
        self.addCleanup(timeline.unregister_canonical_sink, "test-repeat")
        plan = self._plan()
        deliver(plan, _Viewer("Ana", sessions=[_Session({})], knows=[1]))
        deliver(plan, _Viewer("Bo", sessions=[_Session({})]))
        self.assertEqual(seen, [plan])


class TestStorage(PlanTestCase):
    def test_storage_payload_round_trips_and_keeps_references(self):
        plan = RenderPlan(
            kind="emote",
            msg_type="pose",
            subject_id=1,
            sep="\n\n",
            metadata={"surface": "test"},
            blocks=(
                Section(
                    key="body",
                    sep="\n",
                    children=(
                        Line(spans=(CharRef(char_id=1, role="emitter"), TextSpan(" waves."))),
                        Paragraph(spans=(ExitRef(2),)),
                    ),
                ),
            ),
        )
        payload = plan.storage_payload()
        self.assertEqual(payload["schema"], "renderplan.v1")
        rebuilt = RenderPlan.from_storage(payload)
        self.assertEqual(rebuilt.correlation_id, plan.correlation_id)
        # the stored form re-resolves for a *later* reader, not the capture-time one
        # the Section's own sep joins its children; plan.sep separates top-level blocks
        self.assertEqual(resolve(rebuilt, _Viewer("Late", knows=[1])).body, "Kade waves.\nnorth")
        self.assertEqual(resolve(rebuilt, _Viewer("Late")).body, "someone waves.\nnorth")

    def test_entity_ids_reports_referenced_entities(self):
        plan = RenderPlan(
            kind="attack",
            blocks=(Line(spans=(CharRef(char_id=1), ItemRef(3), CharRef(char_id=1))),),
        )
        self.assertEqual(plan.entity_ids(), (1, 3))


class TestTextMetadata(unittest.TestCase):
    def test_extra_outputfunc_metadata_survives_the_node_core(self):
        from evennia.narrative.rendernode import RenderNode, deliver_node

        viewer = _Viewer("Ana", sessions=[_Session({})])
        node = RenderNode(
            kind="text",
            msg_type="say",
            body="hi",
            metadata={"text_kwargs": {"target": "console"}},
        )
        deliver_node(node, viewer)
        self.assertEqual(viewer.calls[0][0][0], ("hi", {"type": "say", "target": "console"}))


class TestTextPlan(PlanTestCase):
    def test_plain_output_still_travels_the_node_core(self):
        viewer = _Viewer("Ana", sessions=[_Session({})])
        deliver(text_plan("Your wounds ache.", msg_type="system"), viewer)
        self.assertEqual(viewer.calls[0][0][0], ("Your wounds ache.", {"type": "system"}))


if __name__ == "__main__":
    unittest.main()
