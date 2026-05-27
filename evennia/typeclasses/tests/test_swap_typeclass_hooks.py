"""Regression test for swap_typeclass run_start_hooks loop."""

from unittest.mock import Mock

from evennia.utils.test_resources import BaseEvenniaTest


class TestSwapTypeclassRunStartHooks(BaseEvenniaTest):
    def test_each_named_hook_fires(self):
        """Each space-separated hook in run_start_hooks runs exactly once."""
        obj = self.obj1
        obj.hook_a = Mock()
        obj.hook_b = Mock()

        obj.swap_typeclass(type(obj), run_start_hooks="hook_a hook_b")

        obj.hook_a.assert_called_once_with()
        obj.hook_b.assert_called_once_with()
