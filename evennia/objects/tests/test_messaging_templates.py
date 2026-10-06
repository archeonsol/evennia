"""Director templates preserve mixed entity and literal substitutions."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from evennia.narrative import plan
from evennia.objects.mixins.messaging import MessagingMixin


class TestMixedTemplates(TestCase):
    """Exercise the room facade without a database or transport."""

    def test_mixed_mapping_formats_for_each_receiver(self):
        """Literal door names do not bypass viewer-specific actor naming."""
        viewers = [SimpleNamespace(ndb=SimpleNamespace()), SimpleNamespace(ndb=SimpleNamespace())]
        actor = SimpleNamespace(
            id=42,
            get_display_name=Mock(
                side_effect=lambda looker: "Kade" if looker is viewers[0] else "a stranger"
            ),
        )
        room = SimpleNamespace(get_message_recipients=lambda exclude: viewers)
        delivered = []

        def capture(node, viewer, **kwargs):
            """Record visible output at the delivery boundary."""
            delivered.append(node.body)

        with (
            patch.object(plan, "deliver_resolved", side_effect=capture),
            patch.object(plan, "deliver_to") as canonical,
        ):
            MessagingMixin.msg_contents(
                room, "{name} opens the {dn}.", mapping={"name": actor, "dn": "steel door"}
            )
        canonical.assert_not_called()
        self.assertEqual(
            delivered, ["Kade opens the steel door.", "A stranger opens the steel door."]
        )

    def test_plan_metadata_opt_in_reaches_the_canonical_plan(self):
        """A producer may mark a broadcast shareable through the outcmd tuple."""
        viewers = [SimpleNamespace(ndb=SimpleNamespace())]
        actor = SimpleNamespace(
            id=42,
            get_display_name=Mock(return_value="Kade"),
            is_typeclass=Mock(return_value=True),
        )
        room = SimpleNamespace(get_message_recipients=lambda exclude: viewers)

        with patch.object(plan, "deliver_to") as canonical:
            MessagingMixin.msg_contents(
                room,
                ("{name} shrugs.", {"plan_metadata": {"frame_shareable": True}}),
                mapping={"name": actor},
            )
        canonical.assert_called_once()
        sent = canonical.call_args[0][0]
        self.assertTrue(sent.metadata.get("frame_shareable"))

    def test_plan_metadata_absent_by_default(self):
        """Ordinary broadcasts keep the per-viewer identity path."""
        viewers = [SimpleNamespace(ndb=SimpleNamespace())]
        actor = SimpleNamespace(
            id=42,
            get_display_name=Mock(return_value="Kade"),
            is_typeclass=Mock(return_value=True),
        )
        room = SimpleNamespace(get_message_recipients=lambda exclude: viewers)

        with patch.object(plan, "deliver_to") as canonical:
            MessagingMixin.msg_contents(room, "{name} shrugs.", mapping={"name": actor})
        canonical.assert_called_once()
        sent = canonical.call_args[0][0]
        self.assertFalse(sent.metadata.get("frame_shareable"))


class TestSentenceOpeningNames(TestCase):
    """A name that opens a sentence is marked to render with a capital."""

    def _refs(self, template):
        from evennia.narrative.render import CharRef
        from evennia.objects.mixins.messaging import _template_plan

        actor = SimpleNamespace(id=42, get_display_name=Mock(), is_typeclass=Mock(return_value=True))
        target = SimpleNamespace(id=43, get_display_name=Mock(), is_typeclass=Mock(return_value=True))
        built = _template_plan(template, {"name": actor, "other": target}, {}, None)
        return [
            (span.role, span.capitalize) for span in built.spans() if isinstance(span, CharRef)
        ]

    def test_a_name_at_the_start_is_capitalized(self):
        self.assertEqual(self._refs("{name} sits down."), [("name", True)])

    def test_a_name_behind_a_colour_code_is_capitalized(self):
        self.assertEqual(self._refs("|w{name}|n sits down."), [("name", True)])

    def test_a_name_mid_sentence_is_not(self):
        self.assertEqual(self._refs("{name} waves at {other}."), [("name", True), ("other", False)])

    def test_a_name_after_a_sentence_end_is(self):
        self.assertEqual(
            self._refs('{name} sits. "Done." {other} nods.'), [("name", True), ("other", True)]
        )

    def test_capitalize_lead_steps_over_codes_and_keeps_handles(self):
        from evennia.narrative.render import capitalize_lead

        for value, expected in (
            ("a tall man", "A tall man"),
            ("|wa tall man|n", "|wA tall man|n"),
            ("|=ea man", "|=eA man"),
            ("@rooter", "@rooter"),
            ("^Z2CVBB", "^Z2CVBB"),
            ("", ""),
        ):
            with self.subTest(value=value):
                self.assertEqual(capitalize_lead(value), expected)
