"""
Tests for location cmdset cache helpers.
"""

from evennia.commands.location_cmdset_cache import (
    bump_cmdset_generation,
    cmdset_generation,
    make_cache_key,
    set_cached_location_cmdsets,
    get_cached_location_cmdsets,
)
from evennia.utils.test_resources import BaseEvenniaTest


class TestLocationCmdsetCache(BaseEvenniaTest):
    def test_generation_bumps_on_cmdset_change(self):
        gen0 = cmdset_generation(self.char1)
        self.char1.cmdset.add("evennia.commands.default.cmdset_character.CharacterCmdSet")
        self.assertGreater(cmdset_generation(self.char1), gen0)
        if self.char1.location:
            self.assertGreaterEqual(cmdset_generation(self.char1.location), gen0)

    def test_cache_roundtrip(self):
        key = make_cache_key(self.char1, self.char1.location)
        sentinel = ["cmdset-list"]
        set_cached_location_cmdsets(key, sentinel)
        self.assertIs(get_cached_location_cmdsets(key), sentinel)
