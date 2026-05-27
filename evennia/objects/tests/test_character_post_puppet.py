"""Regression test for DefaultCharacter.at_post_puppet updating _last_puppet."""

from evennia.utils.test_resources import BaseEvenniaTest


class TestCharacterAtPostPuppetLastPuppet(BaseEvenniaTest):
    def test_at_post_puppet_sets_last_puppet(self):
        """Calling at_post_puppet on a character records it as account's last puppet."""
        # account starts pointing at char1 from test fixture; force a different state.
        self.account.db._last_puppet = None
        self.char1.account = self.account

        self.char1.at_post_puppet()

        self.assertEqual(self.account.db._last_puppet, self.char1)
