"""The per-game Server singleton lock (``evennia.server.server_lock``).

Two Servers against one database defeat the JSONB write-behind layer's
cross-process guarantees, so the lock is the authoritative guard: the Server
holds it for its lifetime, the Portal and launcher probe it before spawning,
and a second Server refuses to start.
"""

import contextlib
import io
import os
import tempfile
import unittest

from django.test import override_settings

from evennia.server import server_lock as sl


class TestServerLock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pidfile = os.path.join(self._tmp.name, "server.pid")

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquire_records_holder_and_blocks_a_second_server(self):
        lock = sl.acquire_server_lock(self.pidfile)
        try:
            self.assertTrue(os.path.isfile(f"{self.pidfile}.lock"))
            self.assertEqual(sl.server_lock_held(self.pidfile), os.getpid())
            with self.assertRaises(sl.ServerAlreadyRunning) as caught:
                sl.acquire_server_lock(self.pidfile)
            self.assertEqual(caught.exception.pid, os.getpid())
        finally:
            lock.release()
        self.assertIsNone(sl.server_lock_held(self.pidfile))

    def test_stale_lock_file_is_not_held(self):
        with open(f"{self.pidfile}.lock", "w", encoding="utf-8") as handle:
            handle.write("999999\t0\n")
        self.assertIsNone(sl.server_lock_held(self.pidfile))

    def test_already_running_reports_the_holder(self):
        err = sl.ServerAlreadyRunning(4321, "/g/server.pid.lock")
        self.assertEqual(err.pid, 4321)
        self.assertIn("4321", str(err))

    def test_pidfile_from_cmd(self):
        self.assertEqual(
            sl.pidfile_from_cmd(["python", "server.py", "--pidfile", "/g/server.pid"]),
            "/g/server.pid",
        )
        self.assertEqual(
            sl.pidfile_from_cmd(["python", "--pidfile=/g/server.pid"]), "/g/server.pid"
        )
        self.assertIsNone(sl.pidfile_from_cmd(["python", "server.py"]))

    def test_refuse_start_reports_to_stderr_and_log(self):
        logfile = os.path.join(self._tmp.name, "server.log")
        stderr = io.StringIO()
        with override_settings(SERVER_LOG_FILE=logfile), contextlib.redirect_stderr(stderr):
            sl.refuse_start("another Server process (pid 5) already owns /g/server.pid")
        self.assertIn("FATAL", stderr.getvalue())
        with open(logfile, encoding="utf-8") as handle:
            self.assertIn("FATAL", handle.read())

    def test_portal_does_not_spawn_into_a_held_lock(self):
        """The Portal must refuse to Popen a Server while the lock is held."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from evennia.server.portal.amp_server import AMPServerProtocol

        with (
            patch.object(sl, "server_lock_held", return_value=4242),
            patch("evennia.server.portal.amp_server.Popen") as popen,
        ):
            AMPServerProtocol.start_server(
                SimpleNamespace(),
                ["python", "server.py", "--pidfile", "/g/server.pid"],
            )
        popen.assert_not_called()
