"""Tests for collectstatic fingerprint cache."""

import os
import shutil
import tempfile
from unittest import mock

from django.test import SimpleTestCase, override_settings

from evennia.server.collectstatic_cache import (compute_static_fingerprint,
                                                read_cached_fingerprint,
                                                static_sources_changed,
                                                write_cached_fingerprint)


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

    def test_content_change_with_preserved_mtime_busts_cache(self):
        # git checkout / cp -p / rsync --times can change content without
        # advancing mtime; size must still bust the cache (f08).
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = os.path.join(tmp, "static")
            os.makedirs(static_dir)
            path = os.path.join(static_dir, "app.css")
            with open(path, "w", encoding="utf-8") as fil:
                fil.write("body {}")
            st = os.stat(path)

            with override_settings(STATICFILES_DIRS=[static_dir]):
                first = compute_static_fingerprint()
                with open(path, "w", encoding="utf-8") as fil:
                    fil.write("body { color: red; }")  # different length
                os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore old mtime
                second = compute_static_fingerprint()
            self.assertNotEqual(first, second)

    def test_static_root_wipe_busts_cache(self):
        # A wiped collect destination must re-trigger collectstatic even when
        # sources are unchanged (f09).
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = os.path.join(tmp, "static")
            os.makedirs(static_dir)
            with open(os.path.join(static_dir, "app.css"), "w", encoding="utf-8") as fil:
                fil.write("body {}")
            root = os.path.join(tmp, "static_root")
            os.makedirs(root)
            with open(os.path.join(root, "app.css"), "w", encoding="utf-8") as fil:
                fil.write("body {}")

            with override_settings(STATICFILES_DIRS=[static_dir], STATIC_ROOT=root):
                populated = compute_static_fingerprint()
                shutil.rmtree(root)
                wiped = compute_static_fingerprint()
            self.assertNotEqual(populated, wiped)

    def test_write_failure_leaves_previous_fingerprint_intact(self):
        # An atomic write must not corrupt the existing fingerprint on failure (f11).
        with tempfile.TemporaryDirectory() as tmp:
            write_cached_fingerprint(tmp, "good")
            with mock.patch(
                "evennia.server.collectstatic_cache.os.replace", side_effect=OSError("boom")
            ):
                with self.assertRaises(OSError):
                    write_cached_fingerprint(tmp, "bad-partial")
            self.assertEqual(read_cached_fingerprint(tmp), "good")
            # no temp file left behind
            server_dir = os.path.join(tmp, "server")
            leftovers = [n for n in os.listdir(server_dir) if n != ".collectstatic_fingerprint"]
            self.assertEqual(leftovers, [])
