"""Tests for collectstatic fingerprint cache."""

import os
import tempfile

from django.test import SimpleTestCase, override_settings

from evennia.server.collectstatic_cache import (
    compute_static_fingerprint,
    read_cached_fingerprint,
    static_sources_changed,
    write_cached_fingerprint,
)


class CollectstaticCacheTest(SimpleTestCase):
    def test_fingerprint_stable_for_unchanged_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = os.path.join(tmp, "static")
            os.makedirs(static_dir)
            with open(os.path.join(static_dir, "app.css"), "w", encoding="utf-8") as fil:
                fil.write("body {}")

            with override_settings(STATICFILES_DIRS=[static_dir]):
                first = compute_static_fingerprint()
                second = compute_static_fingerprint()
            self.assertEqual(first, second)

    def test_static_sources_changed_detects_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = os.path.join(tmp, "static")
            os.makedirs(static_dir)
            with open(os.path.join(static_dir, "app.css"), "w", encoding="utf-8") as fil:
                fil.write("v1")

            with override_settings(STATICFILES_DIRS=[static_dir]):
                fp = compute_static_fingerprint()
                write_cached_fingerprint(tmp, fp)
                self.assertFalse(static_sources_changed(tmp))

                with open(os.path.join(static_dir, "new.css"), "w", encoding="utf-8") as fil:
                    fil.write("v2")
                self.assertTrue(static_sources_changed(tmp))

    def test_read_cached_fingerprint_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_cached_fingerprint(tmp))
