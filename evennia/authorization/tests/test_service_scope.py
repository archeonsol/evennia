"""Render-scoped authorization decision memoization."""

from evennia.authorization.service import authorize, decision_scope, has_capability
from evennia.utils.test_resources import EvenniaTest


class TestDecisionScope(EvenniaTest):
    """One render shares one decision memo; scopes never leak across renders."""

    def test_capability_decisions_are_reused_by_identity(self):
        with decision_scope() as scope:
            first = has_capability(self.account, "engine.help.manage")
            second = has_capability(self.account, "engine.help.manage")

            self.assertEqual(first, second)
            self.assertEqual(len(scope), 1)

    def test_policy_decisions_return_the_same_object(self):
        with decision_scope() as scope:
            first = authorize(self.account, self.obj1, "view")
            second = authorize(self.account, self.obj1, "view")

            self.assertIs(first, second)
            self.assertEqual(len(scope), 1)

    def test_scope_is_dropped_after_exit(self):
        with decision_scope() as scope:
            has_capability(self.account, "engine.help.manage")
            self.assertEqual(len(scope), 1)

        with decision_scope() as fresh:
            self.assertEqual(len(fresh), 0)
            has_capability(self.account, "engine.help.manage")
            self.assertEqual(len(fresh), 1)

    def test_nested_scopes_reuse_the_outer_memo(self):
        with decision_scope() as outer:
            with decision_scope() as inner:
                self.assertIs(inner, outer)
            self.assertIs(inner, outer)

    def test_distinct_questions_do_not_collide(self):
        with decision_scope() as scope:
            has_capability(self.account, "engine.help.manage")
            has_capability(self.account, "engine.object.view")

            self.assertEqual(len(scope), 2)
