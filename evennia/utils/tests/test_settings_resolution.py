"""The shared settings convention: unset or None means the code default."""

from django.test import SimpleTestCase, override_settings

from evennia.utils import utils


class ResolveSettingTest(SimpleTestCase):
    """One resolution rule for every engine tunable."""

    def test_unset_attribute_falls_back_to_the_default(self):
        self.assertEqual(utils.resolve_setting("ST1_TEST_NO_SUCH_SETTING", 5), 5)

    def test_explicit_none_falls_back_to_the_default(self):
        with override_settings(ST1_TEST_CAP=None):
            self.assertEqual(utils.resolve_setting("ST1_TEST_CAP", 5), 5)

    def test_explicit_value_wins_over_the_default(self):
        with override_settings(ST1_TEST_CAP=9):
            self.assertEqual(utils.resolve_setting("ST1_TEST_CAP", 5), 9)

    def test_values_are_clamped_to_the_minimum(self):
        with override_settings(ST1_TEST_CAP=0):
            self.assertEqual(utils.resolve_setting("ST1_TEST_CAP", 64, minimum=1), 1)

    def test_default_is_cast_and_clamped(self):
        self.assertEqual(utils.resolve_setting("ST1_TEST_NO_SUCH_SETTING", 0, minimum=1), 1)

    def test_float_cast(self):
        with override_settings(ST1_TEST_TIMEOUT="2.5"):
            self.assertEqual(
                utils.resolve_setting("ST1_TEST_TIMEOUT", 12.0, cast=float, minimum=1.0), 2.5
            )
        with override_settings(ST1_TEST_TIMEOUT=0):
            self.assertEqual(
                utils.resolve_setting("ST1_TEST_TIMEOUT", 12.0, cast=float, minimum=1.0), 1.0
            )
