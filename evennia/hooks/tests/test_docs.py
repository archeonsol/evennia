"""Tests for the doc generator."""

from evennia.hooks import hook
from evennia.hooks.docs import (
    _rewrite_doc,
    regenerate_doc_files,
    render_misshapen,
    render_return_contracts,
    render_section,
)
from evennia.hooks.registry import _REGISTRY, _reset_registry_for_tests
from evennia.utils.test_resources import EvenniaTestCase


class RenderReturnContractsTest(EvenniaTestCase):
    def setUp(self):
        super().setUp()
        self._saved = dict(_REGISTRY)
        _reset_registry_for_tests()

        class _Demo:
            @hook(
                event="puppet",
                phase="pre",
                actor="target",
                returns="veto",
                discipline="public",
                fires_from=(),
                notes="Veto aborts attach.",
            )
            def at_pre_puppet(self, *a, **k):
                pass

            @hook(
                event="look",
                phase="composite",
                actor="target",
                returns="content",
                discipline="public",
                fires_from=(),
                notes="Per-looker name.",
            )
            def get_display_name(self, *a, **k):
                pass

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved)
        super().tearDown()

    def test_groups_by_returns_category(self):
        out = render_return_contracts()
        self.assertIn("### Veto", out)
        self.assertIn("### Content", out)
        self.assertIn("`at_pre_puppet`", out)
        self.assertIn("`get_display_name`", out)
        self.assertIn("Veto aborts attach.", out)

    def test_skips_empty_categories(self):
        out = render_return_contracts()
        # No specs declare returns="transform" in this fixture.
        self.assertNotIn("### Transform", out)

    def test_escapes_pipe_in_notes(self):
        class _Pipey:
            @hook(
                event="x",
                phase="pre",
                actor="self",
                returns="veto",
                discipline="public",
                fires_from=(),
                notes="A | with | pipes",
            )
            def at_pre_x(self, *a, **k):
                pass

        out = render_return_contracts()
        self.assertIn(r"A \| with \| pipes", out)


class RenderMisshapenTest(EvenniaTestCase):
    def setUp(self):
        super().setUp()
        self._saved = dict(_REGISTRY)
        _reset_registry_for_tests()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved)
        super().tearDown()

    def test_lists_self_flagged_specs(self):
        class _Mis:
            @hook(
                event="msg",
                phase="composite",
                actor="self",
                returns="veto",
                discipline="public",
                fires_from=(),
                notes="Misshapen: at_<event> name with veto contract.",
            )
            def at_msg_send(self, *a, **k):
                pass

        out = render_misshapen()
        self.assertIn("Self-flagged in spec notes", out)
        self.assertIn("at_msg_send", out)

    def test_reports_empty_when_clean(self):
        out = render_misshapen()
        self.assertIn("_None.", out)


class RewriteDocTest(EvenniaTestCase):
    def setUp(self):
        super().setUp()
        self._saved = dict(_REGISTRY)
        _reset_registry_for_tests()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved)
        super().tearDown()

    def test_replaces_marked_block(self):
        doc = (
            "intro\n\n"
            "<!-- hooks-gen:start return-contracts -->\n"
            "OLD CONTENT\n"
            "<!-- hooks-gen:end -->\n\n"
            "outro\n"
        )
        out = _rewrite_doc(doc)
        self.assertNotIn("OLD CONTENT", out)
        self.assertIn("<!-- hooks-gen:start return-contracts -->", out)
        self.assertIn("<!-- hooks-gen:end -->", out)
        # Surrounding prose preserved.
        self.assertTrue(out.startswith("intro\n\n"))
        self.assertTrue(out.endswith("outro\n"))

    def test_unknown_section_left_alone(self):
        doc = "<!-- hooks-gen:start no-such-section -->\nkeep me\n<!-- hooks-gen:end -->\n"
        self.assertEqual(_rewrite_doc(doc), doc)

    def test_render_section_unknown_raises(self):
        with self.assertRaises(KeyError):
            render_section("no-such-section")


class PublishedDocsInSyncTest(EvenniaTestCase):
    """Fails when published docs are stale relative to the registry."""

    def test_docs_match_registry(self):
        stale = regenerate_doc_files(write=False)
        self.assertEqual(
            stale,
            [],
            "Hook docs are stale; run `python -m evennia.hooks.docs --write`. "
            f"Affected: {[str(p) for p in stale]}",
        )
