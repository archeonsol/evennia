"""Runtime identifier shape and uniqueness."""

from django.test import SimpleTestCase

from evennia.utils.fast_ids import new_runtime_id


class TestRuntimeIds(SimpleTestCase):
    def test_ids_are_unique_fixed_width_hex(self):
        identifiers = [new_runtime_id() for _ in range(10_000)]

        self.assertEqual(len(set(identifiers)), len(identifiers))
        self.assertTrue(all(len(identifier) == 32 for identifier in identifiers))
        self.assertTrue(all(int(identifier, 16) >= 0 for identifier in identifiers))
