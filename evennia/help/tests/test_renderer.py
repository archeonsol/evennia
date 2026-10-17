"""Tests for manual-topic behavior in the action help renderer."""

import re
from unittest import TestCase, mock

from django.test import override_settings
from parameterized import parameterized

from evennia.help import catalog, formatters, renderer
from evennia.utils import ansi, create
from evennia.utils.test_resources import EvenniaTest

_SEPARATOR_LINE = re.compile(r"^-+$", re.MULTILINE)

_SUBTOPIC_ENTRY = (
    "Main help text\n\n"
    "# SUBTOPICS\n\n"
    "## creating extra stuff\n\n"
    "Help on creating extra stuff.\n\n"
    "### subsubtopic\n\n"
    "A subsubtopic text\n\n"
    "## Something else\n\n"
    "Something else\n\n"
    "## More\n\n"
    "Another text\n\n"
    "### Second-More\n\n"
    "The Second More text.\n\n"
    "#### More again\n\n"
    "Even more text.\n\n"
    "#### Third more\n\n"
    "Third more text\n"
)
_CREATING = (
    "Help for test/creating extra stuff\n\n"
    "Help on creating extra stuff.\n\n"
    "Subtopics:\n"
    "  test/creating extra stuff/subsubtopic"
)
_SUBSUB = "Help for test/creating extra stuff/subsubtopic\n\nA subsubtopic text"
_SECOND_MORE = (
    "Help for test/more/second-more\n\n"
    "The Second More text.\n\n"
    "Subtopics:\n"
    "  test/more/second-more/more again  test/more/second-more/third more"
)


class _Entry:
    """Minimal file/DB help entry surface used by the renderer."""

    def __init__(self, key, text, aliases=(), category="general"):
        self.key = key
        self.entrytext = text
        self.aliases = list(aliases)
        self.help_category = category


class TestManualHelpRenderer(TestCase):
    """Keep ordinary help manual-only when action indexing is disabled."""

    @override_settings(HELP_INDEX_ACTIONS=False, HELP_INDEX_ACTIONS_FOR_STAFF=False)
    def test_manual_commands_topic_skips_action_catalog_and_exact_search(self):
        entry = _Entry("commands", "Manually authored command guidance.")
        caller = mock.Mock()

        with (
            mock.patch.object(catalog, "actor_for_help", return_value=object()),
            mock.patch.object(
                catalog,
                "collect_action_help_topics",
                side_effect=AssertionError("manual help collected action topics"),
            ),
            mock.patch.object(
                formatters.HelpFormatter,
                "collect_topics",
                return_value=({}, {}, {"commands": entry}),
            ),
            mock.patch.object(
                formatters.HelpFormatter,
                "do_search",
                side_effect=AssertionError("exact manual help built a search index"),
            ),
        ):
            renderer.render_help(caller, topic="commands")

        output = str(caller.msg.call_args.args[0])
        self.assertIn("Manually authored command guidance.", output)

    def test_exact_manual_precedence(self):
        db_exact = _Entry("target", "DB exact")
        db_same_key = _Entry("shared", "DB shared")
        db_alias = _Entry("db-alias-owner", "DB alias", aliases=("alias",))
        file_alias_for_target = _Entry("file-other", "File alias", aliases=("target",))
        file_same_key = _Entry("shared", "File shared")
        file_alias = _Entry("file-alias-owner", "File alias owner", aliases=("alias",))
        db_topics = {entry.key: entry for entry in (db_exact, db_same_key, db_alias)}
        file_topics = {
            entry.key: entry for entry in (file_alias_for_target, file_same_key, file_alias)
        }

        self.assertIs(renderer._exact_manual_topic("target", db_topics, file_topics), db_exact)
        self.assertIs(
            renderer._exact_manual_topic("shared", db_topics, file_topics),
            file_same_key,
        )
        self.assertIs(renderer._exact_manual_topic("alias", db_topics, file_topics), file_alias)


class TestRenderHelpEntries(EvenniaTest):
    """Render DB help entries and walk their ``# SUBTOPICS`` markup."""

    evennia_fixtures = {"char1"}
    maxDiff = None

    def _render(self, topic="", subtopics=None):
        self.char1.msg = mock.Mock()
        renderer.render_help(self.char1, topic=topic, subtopics=subtopics)
        text = ansi.strip_mxp(ansi.strip_ansi(str(self.char1.msg.call_args.args[0])))
        return _SEPARATOR_LINE.sub("", text).strip()

    def test_bare_index_lists_db_topics(self):
        create.create_help_entry("stabletopic", "Some help body.", category="General")
        self.assertIn("stabletopic", self._render())

    def test_entry_view(self):
        create.create_help_entry("testhelp", "This is a test", category="General")
        self.assertEqual(self._render("testhelp"), "Help for testhelp\n\nThis is a test")

    @parameterized.expand(
        [
            (
                "main entry",
                [],
                (
                    "Help for test\n\n"
                    "Main help text\n\n"
                    "Subtopics:\n"
                    "  test/creating extra stuff  test/something else  test/more"
                ),
            ),
            ("full subtopic", ["creating extra stuff"], _CREATING),
            ("startswith match", ["creating"], _CREATING),
            ("partial match", ["extra"], _CREATING),
            ("partial subsubtopic", ["extra", "subsubtopic"], _SUBSUB),
            ("partial both levels", ["creating extra", "subsub"], _SUBSUB),
            (
                "heading case folds",
                ["something else"],
                "Help for test/something else\n\nSomething else",
            ),
            (
                "subtopic with children",
                ["more"],
                "Help for test/more\n\nAnother text\n\nSubtopics:\n  test/more/second-more",
            ),
            ("nested heading case folds", ["more", "second-more"], _SECOND_MORE),
            ("nested partial match", ["more", "-more"], _SECOND_MORE),
            (
                "third level",
                ["more", "second", "more again"],
                "Help for test/more/second-more/more again\n\nEven more text.",
            ),
            (
                "third level partial",
                ["more", "second", "third"],
                "Help for test/more/second-more/third more\n\nThird more text",
            ),
        ]
    )
    def test_subtopic_fetch(self, _name, subtopics, expected):
        create.create_help_entry("test", _SUBTOPIC_ENTRY, category="General")
        self.assertEqual(self._render("test", subtopics), expected)
