"""Tests for manual-topic behavior in the action help renderer."""

from unittest import TestCase, mock

from django.test import override_settings

from evennia.help import catalog, formatters, renderer


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
