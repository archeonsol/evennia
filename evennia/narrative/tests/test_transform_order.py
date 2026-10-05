"""The order delivery transforms run in, and the cache of that order.

``transforms()`` is called for every node for every viewer, so the sorted tuple
is kept until the registry changes. The tests keep to their own keys, so what a
game or another test registered does not matter.
"""

from unittest import TestCase

from evennia.narrative import pipeline

_KEYS = ("test-order-a", "test-order-b", "test-order-c")


def _mine():
    """The keys this module registered, in the order they would run."""
    return [key for key, _fn in pipeline.transforms() if key in _KEYS]


def _noop(node, viewer, ctx):
    return node


class TransformOrderTest(TestCase):
    def setUp(self):
        for key in _KEYS:
            self.addCleanup(pipeline.unregister_transform, key)

    def test_a_lower_priority_runs_first_and_a_tie_runs_by_key(self):
        pipeline.register_transform("test-order-c", _noop, priority=-5)
        pipeline.register_transform("test-order-b", _noop, priority=10)
        pipeline.register_transform("test-order-a", _noop, priority=10)

        self.assertEqual(_mine(), ["test-order-c", "test-order-a", "test-order-b"])

    def test_the_same_tuple_comes_back_while_the_registry_is_unchanged(self):
        pipeline.register_transform("test-order-a", _noop)

        self.assertIs(pipeline.transforms(), pipeline.transforms())

    def test_a_new_registration_is_seen_by_the_next_call(self):
        pipeline.register_transform("test-order-a", _noop, priority=1)
        self.assertEqual(_mine(), ["test-order-a"])

        pipeline.register_transform("test-order-b", _noop, priority=0)

        self.assertEqual(_mine(), ["test-order-b", "test-order-a"])

    def test_a_removal_is_seen_by_the_next_call(self):
        pipeline.register_transform("test-order-a", _noop)
        pipeline.register_transform("test-order-b", _noop)
        self.assertEqual(_mine(), ["test-order-a", "test-order-b"])

        removed = pipeline.unregister_transform("test-order-a")

        self.assertIs(removed, _noop)
        self.assertEqual(_mine(), ["test-order-b"])

    def test_removing_what_is_not_registered_changes_nothing(self):
        pipeline.register_transform("test-order-a", _noop)
        before = pipeline.transforms()

        self.assertIsNone(pipeline.unregister_transform("test-order-missing"))

        self.assertIs(pipeline.transforms(), before)

    def test_an_override_replaces_the_function_and_moves_its_place(self):
        def other(node, viewer, ctx):
            return node

        pipeline.register_transform("test-order-a", _noop, priority=1)
        pipeline.register_transform("test-order-b", _noop, priority=2)
        self.assertEqual(_mine(), ["test-order-a", "test-order-b"])

        pipeline.register_transform("test-order-a", other, priority=3, override=True)

        self.assertEqual(_mine(), ["test-order-b", "test-order-a"])
        self.assertIs(dict(pipeline.transforms())["test-order-a"], other)

    def test_a_refused_registration_leaves_the_order_alone(self):
        pipeline.register_transform("test-order-a", _noop)
        before = pipeline.transforms()

        with self.assertRaises(ValueError):
            pipeline.register_transform("test-order-a", _noop)

        self.assertIs(pipeline.transforms(), before)
