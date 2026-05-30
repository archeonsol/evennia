"""Tests for the lazy flat-API registry in `evennia/__init__.py`.

The flat API is exposed via PEP 562 module-level `__getattr__`. Each
declared export must resolve on first access and be cached back into
module globals. Names not in `__all__` raise `AttributeError`.
"""

import importlib

from django.test import SimpleTestCase

import evennia


class TestFlatApiRegistry(SimpleTestCase):
    """`evennia.__all__` is the declared flat-API surface."""

    def test_all_is_sorted_and_unique(self):
        self.assertEqual(evennia.__all__, sorted(set(evennia.__all__)))

    def test_lazy_exports_subset_of_all(self):
        # registry-driven names must be advertised in __all__
        self.assertTrue(set(evennia._LAZY_EXPORTS).issubset(set(evennia.__all__)))

    def test_init_populated_subset_of_all(self):
        self.assertTrue(set(evennia._INIT_POPULATED).issubset(set(evennia.__all__)))

    def test_no_overlap_between_lazy_and_init(self):
        # a name should be claimed by exactly one mechanism
        self.assertEqual(set(evennia._LAZY_EXPORTS) & set(evennia._INIT_POPULATED), set())

    def test_unknown_attribute_raises(self):
        with self.assertRaises(AttributeError):
            evennia.this_name_does_not_exist


class TestFlatApiLazyResolution(SimpleTestCase):
    """Each registry entry resolves to a real object on first access."""

    def test_every_lazy_export_resolves(self):
        for name in list(evennia._LAZY_EXPORTS):
            value = getattr(evennia, name)
            self.assertIsNotNone(value, f"lazy export {name!r} resolved to None")

    def test_resolution_caches_into_globals(self):
        # pick a name that the registry owns and force-evict it so the
        # cache pathway can be exercised cleanly
        name = "EvForm"
        evennia.__dict__.pop(name, None)
        self.assertNotIn(name, evennia.__dict__)
        first = getattr(evennia, name)
        self.assertIn(name, evennia.__dict__)
        second = getattr(evennia, name)
        self.assertIs(first, second)

    def test_lazy_load_fires_only_once(self):
        # second access should not re-import — verified by patching
        # importlib.import_module and confirming it's untouched on a hit
        name = "EvTable"
        getattr(evennia, name)  # ensure cached
        from unittest.mock import patch

        with patch("evennia.importlib.import_module") as m:
            getattr(evennia, name)
            m.assert_not_called()

    def test_settings_resolves_to_django_settings(self):
        from django.conf import settings as django_settings

        self.assertIs(evennia.settings, django_settings)

    def test_submodule_entries_return_modules(self):
        # registry specs like ".utils.ansi:" bind the module itself
        import evennia.utils.ansi as ansi_mod
        import evennia.utils.gametime as gametime_mod

        self.assertIs(evennia.ansi, ansi_mod)
        self.assertIs(evennia.gametime, gametime_mod)
