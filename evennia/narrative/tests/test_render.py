"""Unit tests for the span/render primitive (R1 slice 1 foundation).

Pure, no DB. Pins: literal passthrough, per-viewer resolution via the resolver
seam (including perception fallback), and span serialization round-trip for
stored/replayable content.
"""

import unittest

from evennia.narrative.render import (CharRef, KeyResolver, PipelineResolver,
                                      PronounRef, Section, SectionedView,
                                      SpeechSpan, TextSpan, ViewerContext,
                                      render_spans, span_from_dict,
                                      span_to_dict)


class _Obj:
    def __init__(self, key):
        self.key = key


class TestKeyResolver(unittest.TestCase):
    def test_literal_and_name_and_possessive(self):
        objs = {1: _Obj("Kade")}
        resolver = KeyResolver(lookup=objs.get)
        spans = [
            TextSpan("smiles at "),
            CharRef(char_id=1),
            TextSpan(" and takes "),
            CharRef(char_id=1, possessive=True),
            TextSpan(" hand"),
        ]
        out = render_spans(spans, ViewerContext(), resolver)
        self.assertEqual(out, "smiles at Kade and takes Kade's hand")

    def test_pronoun_and_speech_defaults(self):
        resolver = KeyResolver()
        spans = [
            PronounRef(referent_id=1, form="subject", original="she"),
            TextSpan(" says "),
            SpeechSpan(text="hello", lang="cant"),
        ]
        out = render_spans(spans, ViewerContext(), resolver)
        self.assertEqual(out, 'she says "hello"')


class TestTextHook(unittest.TestCase):
    def test_text_hook_transforms_textspans_only(self):
        objs = {1: _Obj("Kade")}

        class _Rot(KeyResolver):
            def text(self, span, ctx):
                return span.text.upper()

        spans = [TextSpan("waves at "), CharRef(char_id=1)]
        out = render_spans(spans, ViewerContext(), _Rot(lookup=objs.get))
        # TextSpan transformed; the CharRef name is untouched (structure kept)
        self.assertEqual(out, "WAVES AT Kade")


class TestPerViewerResolver(unittest.TestCase):
    """A custom resolver proves per-viewer naming + perception gating live at the
    single render seam, not baked at emit time."""

    class _Resolver:
        def char(self, ref, ctx):
            # viewer sees themselves as "you"; can't perceive => "someone"
            if ctx.get("self_id") == ref.char_id:
                return "your" if ref.possessive else "you"
            if ref.char_id in ctx.get("unseen", ()):
                return "someone"
            name = ctx.get("names", {}).get(ref.char_id, str(ref.char_id))
            return name + ("'s" if ref.possessive else "")

        def pron(self, ref, ctx):
            if ref.referent_id == ctx.get("self_id"):
                return "you"
            return ref.original

        def speech(self, ref, ctx):
            return f'"{ref.text}"'

    def _spans(self):
        return [CharRef(char_id=1), TextSpan(" waves at "), CharRef(char_id=2)]

    def test_same_tree_renders_differently_per_viewer(self):
        resolver = self._Resolver()
        spans = self._spans()
        # viewer 1 (the emitter) sees themselves as "you", knows 2 as "Vex"
        ctx1 = ViewerContext(extras={"self_id": 1, "names": {2: "Vex"}, "unseen": ()})
        # viewer 3 recognizes 1 as "Kade" but cannot perceive 2 (stealth/dark)
        ctx3 = ViewerContext(extras={"self_id": 3, "names": {1: "Kade"}, "unseen": (2,)})
        self.assertEqual(render_spans(spans, ctx1, resolver), "you waves at Vex")
        self.assertEqual(render_spans(spans, ctx3, resolver), "Kade waves at someone")

    def test_retroactive_naming_from_stored_tree(self):
        # Store the tree, then resolve later once recognition changed: the same
        # stored spans yield the updated name. This is the recordings win.
        resolver = self._Resolver()
        stored = [span_to_dict(s) for s in self._spans()]
        rebuilt = [span_from_dict(d) for d in stored]
        before = ViewerContext(extras={"self_id": 9, "names": {}, "unseen": ()})
        after = ViewerContext(extras={"self_id": 9, "names": {1: "Kade"}, "unseen": ()})
        self.assertEqual(render_spans(rebuilt, before, resolver), "1 waves at 2")
        self.assertEqual(render_spans(rebuilt, after, resolver), "Kade waves at 2")


class TestSerialization(unittest.TestCase):
    def test_round_trip_all_span_kinds(self):
        spans = [
            TextSpan("x"),
            CharRef(char_id=7, role="emitter", possessive=True),
            PronounRef(referent_id=7, form="poss_det", original="her"),
            SpeechSpan(text="hi", lang="en"),
        ]
        rebuilt = [span_from_dict(span_to_dict(s)) for s in spans]
        self.assertEqual(rebuilt, spans)


class TestSectionedView(unittest.TestCase):
    """The block/sectioned-view primitive: ordered sections + group-driven flatten."""

    def test_blocks_returns_nonempty_in_order(self):
        view = SectionedView(order=("a", "b", "c"), a="A", c="C")
        self.assertEqual(view.blocks(), [("a", "A"), ("c", "C")])
        self.assertEqual(view.text_of("b"), "")

    def test_default_flatten_one_block_per_section(self):
        view = SectionedView(order=("a", "b"), a="A", b="B")
        self.assertEqual(view.flatten(), "A\n\nB")

    def test_group_flatten_reproduces_grouped_join(self):
        # header+desc joined by \n; mid its own block; tail joined by \n.
        view = SectionedView(
            order=("header", "desc", "atmos", "chars", "exits"),
            header="Room",
            desc="A place.",
            atmos="It is raining.",
            chars="Kade is here.",
            exits="Exits: north.",
        )
        groups = [["header", "desc"], ["atmos"], ["chars", "exits"]]
        self.assertEqual(
            view.flatten(groups),
            "Room\nA place.\n\nIt is raining.\n\nKade is here.\nExits: north.",
        )

    def test_empty_group_vanishes(self):
        view = SectionedView(order=("header", "desc", "atmos"), header="Room", desc="A place.")
        groups = [["header", "desc"], ["atmos"]]
        # atmos empty => its block drops, no trailing separator.
        self.assertEqual(view.flatten(groups), "Room\nA place.")

    def test_set_can_clear_a_section(self):
        view = SectionedView(order=("a", "atmos"), a="A", atmos="weather")
        view.set("atmos", "")  # the weather-gating one-liner
        self.assertEqual(view.flatten([["a"], ["atmos"]]), "A")

    def test_section_carries_optional_spans(self):
        view = SectionedView(order=("desc",))
        view.set("desc", "A place.", spans=[TextSpan("A place.")])
        self.assertTrue(view.section("desc"))
        self.assertEqual(view.section("desc").spans[0].text, "A place.")


class TestPipelineResolver(unittest.TestCase):
    """Base namers + ordered passes, with the ``final`` short-circuit."""

    def _resolver(self):
        r = PipelineResolver()

        # namer: self->you (final); concealed->someone (final); else base (not final)
        def namer(ref, ctx):
            if ctx.get("self_id") == ref.char_id:
                return "you", True
            if ref.char_id in ctx.get("unseen", ()):
                return "someone", True
            return ctx.get("names", {}).get(ref.char_id, str(ref.char_id)), False

        r.set_char_namer(namer)
        return r

    def test_passes_fold_in_order_over_base(self):
        r = self._resolver()

        class _Brackets:
            def char(self, value, ref, ctx):
                return f"[{value}]"

        class _Upper:
            def char(self, value, ref, ctx):
                return value.upper()

        r.add_pass(_Brackets()).add_pass(_Upper())
        ctx = ViewerContext(extras={"self_id": 9, "names": {1: "Kade"}})
        self.assertEqual(r.char(CharRef(char_id=1), ctx), "[KADE]")

    def test_final_namer_short_circuits_passes(self):
        r = self._resolver()

        class _Distort:
            def char(self, value, ref, ctx):
                return value + "!!!"

        r.add_pass(_Distort())
        # self ("you") and concealed ("someone") are final => pass never applies;
        # a perceivable named ref does get the pass.
        self_ctx = ViewerContext(extras={"self_id": 1})
        unseen_ctx = ViewerContext(extras={"self_id": 9, "unseen": (1,)})
        seen_ctx = ViewerContext(extras={"self_id": 9, "names": {1: "Kade"}})
        self.assertEqual(r.char(CharRef(char_id=1), self_ctx), "you")
        self.assertEqual(r.char(CharRef(char_id=1), unseen_ctx), "someone")
        self.assertEqual(r.char(CharRef(char_id=1), seen_ctx), "Kade!!!")

    def test_text_pass_and_default_speech(self):
        r = PipelineResolver()

        class _Rot:
            def text(self, value, span, ctx):
                return value.replace("threat", "footsteps")

        r.add_pass(_Rot())
        ctx = ViewerContext()
        self.assertEqual(r.text(TextSpan("a threat nears"), ctx), "a footsteps nears")
        # no speech namer => engine default quoting, unquoted honoured
        self.assertEqual(r.speech(SpeechSpan(text="hi"), ctx), '"hi"')
        self.assertEqual(r.speech(SpeechSpan(text="hi", quoted=False), ctx), "hi")


class TestNamedCore(unittest.TestCase):
    def test_register_dispatch_and_missing(self):
        from evennia.narrative import pipeline

        @pipeline.register_producer("greet_test")
        def _greet(obj, viewer, punct="!"):
            return f"{obj}->{viewer}{punct}"

        try:
            self.assertEqual(pipeline.render("A", "B", kind="greet_test", punct="?"), "A->B?")
            self.assertIn("greet_test", pipeline.producers())
            with self.assertRaises(LookupError):
                pipeline.render("A", "B", kind="no_such_kind_xyz")
        finally:
            pipeline._PRODUCERS.pop("greet_test", None)

    def test_re_registration_raises_without_override(self):
        from evennia.narrative import pipeline

        pipeline.register_producer("dup_test", lambda o, v: "one")
        try:
            with self.assertRaises(ValueError):
                pipeline.register_producer("dup_test", lambda o, v: "two")
            # explicit override replaces the producer
            pipeline.register_producer("dup_test", lambda o, v: "three", override=True)
            self.assertEqual(pipeline.render("A", "B", kind="dup_test"), "three")
        finally:
            pipeline._PRODUCERS.pop("dup_test", None)


if __name__ == "__main__":
    unittest.main()
