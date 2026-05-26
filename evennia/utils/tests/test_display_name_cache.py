"""
Tests for display name cache.
"""

from django.test import override_settings
from mock import patch

from evennia.utils.display_name_cache import (
    bump_recog_generation,
    cached_get_display_name,
    invalidate_display_name_cache,
)
from evennia.utils.test_resources import BaseEvenniaTest


class TestDisplayNameCache(BaseEvenniaTest):
    @override_settings(MSG_DISPLAY_NAME_CACHE_ENABLED=True, MSG_DISPLAY_NAME_CACHE_TTL=300)
    def test_cache_avoids_repeat_call(self):
        with patch.object(
            self.char2, "get_display_name", wraps=self.char2.get_display_name
        ) as mock_name:
            cached_get_display_name(self.char2, self.char1)
            cached_get_display_name(self.char2, self.char1)
            self.assertEqual(mock_name.call_count, 1)

    @override_settings(MSG_DISPLAY_NAME_CACHE_ENABLED=True)
    def test_invalidate_clears_cache(self):
        with patch.object(
            self.char2, "get_display_name", wraps=self.char2.get_display_name
        ) as mock_name:
            cached_get_display_name(self.char2, self.char1)
            invalidate_display_name_cache(self.char1)
            cached_get_display_name(self.char2, self.char1)
            self.assertEqual(mock_name.call_count, 2)

    @override_settings(MSG_DISPLAY_NAME_CACHE_ENABLED=True, MSG_DISPLAY_NAME_CACHE_TTL=300)
    def test_recog_generation_bypasses_cache(self):
        with patch.object(
            self.char2, "get_display_name", wraps=self.char2.get_display_name
        ) as mock_name:
            cached_get_display_name(self.char2, self.char1)
            bump_recog_generation(self.char1)
            cached_get_display_name(self.char2, self.char1)
            self.assertEqual(mock_name.call_count, 2)
