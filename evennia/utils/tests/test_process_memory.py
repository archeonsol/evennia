"""Tests for reading this process's resident memory."""

import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.utils import process_memory


class TestResidentMemory(SimpleTestCase):
    def test_reads_the_resident_pages_from_a_statm_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".statm", delete=False) as handle:
            handle.write("123456 51200 900 10 0 4000 0\n")

        self.assertAlmostEqual(process_memory.rss_from_statm(handle.name, page_size=4096), 200.0)

    def test_an_unreadable_statm_file_is_unknown_not_an_error(self):
        self.assertIsNone(process_memory.rss_from_statm("/no/such/statm", page_size=4096))

    def test_prefers_the_current_size_over_the_peak(self):
        fake = SimpleNamespace(
            Process=lambda pid: SimpleNamespace(
                memory_info=lambda: SimpleNamespace(rss=600 * 1024 * 1024)
            )
        )

        with patch.dict(sys.modules, {"psutil": fake}):
            self.assertAlmostEqual(process_memory.current_rss_mb(), 600.0)

    def test_falls_back_to_statm_when_psutil_is_absent(self):
        with (
            patch.dict(sys.modules, {"psutil": None}),
            patch.object(process_memory, "rss_from_statm", return_value=321.0),
        ):
            self.assertEqual(process_memory.current_rss_mb(), 321.0)

    def test_the_idmapper_reads_memory_through_the_same_function(self):
        from evennia.utils.idmapper import models as idmapper

        self.assertIs(idmapper._current_rss_mb, process_memory.current_rss_mb)
