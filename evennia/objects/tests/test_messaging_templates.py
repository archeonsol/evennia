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
            delivered, ["Kade opens the steel door.", "a stranger opens the steel door."]
        )
