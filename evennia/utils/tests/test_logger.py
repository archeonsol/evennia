import unittest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from evennia.utils import logger
from evennia.utils.logger import mask_sensitive_input


class TestLogFileNoLoop(unittest.TestCase):
    """
    log_file must write synchronously when no event loop is running (early
    boot, synchronous test setUp). Deferring to a thread there either raises
    "no running event loop" or queues on a loop that never runs, silently
    dropping the line.
    """

    @patch("evennia.utils.logger._future_errback")
    @patch("evennia.utils.logger._open_log_file")
    @patch("evennia.utils.logger.clock")
    def test_no_loop_writes_synchronously(self, mock_clock, mock_open, mock_fe):
        mock_clock.loop_running.return_value = False
        handle = MagicMock()
        mock_open.return_value = handle
        logger.log_file("hello", filename="test.log")
        mock_clock.defer_to_thread.assert_not_called()
        handle.write.assert_called_once()
        handle.flush.assert_called_once()

    @patch("evennia.utils.logger._future_errback")
    @patch("evennia.utils.logger._open_log_file")
    @patch("evennia.utils.logger.clock")
    def test_running_loop_defers_to_thread(self, mock_clock, mock_open, mock_fe):
        mock_clock.loop_running.return_value = True
        handle = MagicMock()
        mock_open.return_value = handle
        logger.log_file("hello", filename="test.log")
        mock_clock.defer_to_thread.assert_called_once()
        handle.write.assert_not_called()


class TestMaskSensitiveInput(unittest.TestCase):
    def test_connect(self):
        self.assertEqual(
            mask_sensitive_input("connect johnny password123"), "connect johnny ***********"
        )
        self.assertEqual(
            mask_sensitive_input('connect "johnny five" "password 123"'),
            'connect "johnny five" **************',
        )
        self.assertEqual(mask_sensitive_input("conn johnny pass"), "conn johnny ********")

    def test_create(self):
        self.assertEqual(
            mask_sensitive_input("create johnny password123"), "create johnny ***********"
        )
        self.assertEqual(mask_sensitive_input("cr johnny pass"), "cr johnny ********")

    def test_password(self):
        self.assertEqual(
            mask_sensitive_input("@password oldpassword = newpassword"),
            "@password *************************",
        )
        self.assertEqual(
            mask_sensitive_input("password oldpassword newpassword"),
            "password ***********************",
        )

    def test_userpassword(self):
        self.assertEqual(
            mask_sensitive_input("@userpassword johnny = password234"),
            "@userpassword johnny = ***********",
        )

    def test_non_sensitive(self):
        safe = "say connect johnny password123"
        self.assertEqual(mask_sensitive_input(safe), safe)

    @override_settings(AUDIT_MASKS=[{"mylogin": r"^mylogin\s+\w+\s+(?P<secret>.+)$"}])
    def test_override_settings_masks(self):
        self.assertEqual(
            mask_sensitive_input("mylogin johnny customsecret"), "mylogin johnny ************"
        )
        # default masks are replaced when overridden.
        self.assertEqual(
            mask_sensitive_input("connect johnny password123"),
            "connect johnny password123",
        )


class TestPruneRotatedLogs(unittest.TestCase):
    """prune_rotated_logs deletes rotated backups only, per retention and count."""

    def setUp(self):
        import shutil
        import tempfile

        from evennia.utils import logger

        self.logger = logger
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self._old_logdir = logger._LOGDIR
        self._old_last_run = logger._LOG_PRUNE_LAST_RUN
        logger._LOGDIR = self.tmpdir
        logger._LOG_PRUNE_LAST_RUN = 0
        self.addCleanup(self._restore_globals)

    def _restore_globals(self):
        self.logger._LOGDIR = self._old_logdir
        self.logger._LOG_PRUNE_LAST_RUN = self._old_last_run

    def _make(self, name, age_days=0.0):
        import os
        import time

        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as fil:
            fil.write("log line\n")
        mtime = time.time() - age_days * 86400
        os.utime(path, (mtime, mtime))
        return path

    def _names(self):
        import os

        return sorted(os.listdir(self.tmpdir))

    @override_settings(LOG_ROTATED_RETENTION_DAYS=14, LOG_ROTATED_MAX_BACKUPS=0)
    def test_retention_deletes_old_keeps_new_never_active(self):
        self._make("server.log", age_days=30)  # active log: never pruned
        self._make("server.log.2026_05_01__1", age_days=20)
        self._make("server.log.2026_06_10__1", age_days=1)
        self._make("game.log.2026_05_01__1", age_days=20)  # not in prune names

        deleted = self.logger.prune_rotated_logs(force=True)

        self.assertEqual(deleted, 1)
        self.assertEqual(
            self._names(),
            ["game.log.2026_05_01__1", "server.log", "server.log.2026_06_10__1"],
        )

    @override_settings(LOG_ROTATED_RETENTION_DAYS=0, LOG_ROTATED_MAX_BACKUPS=2)
    def test_max_backups_counted_per_base_name(self):
        for age in (1, 2, 3, 4):
            self._make(f"server.log.2026_06_0{age}__1", age_days=age)
        for age in (1, 2, 3):
            self._make(f"portal.log.2026_06_0{age}__1", age_days=age)

        deleted = self.logger.prune_rotated_logs(force=True)

        self.assertEqual(deleted, 3)  # 2 oldest server + 1 oldest portal
        self.assertEqual(
            self._names(),
            [
                "portal.log.2026_06_01__1",
                "portal.log.2026_06_02__1",
                "server.log.2026_06_01__1",
                "server.log.2026_06_02__1",
            ],
        )

    @override_settings(LOG_ROTATED_RETENTION_DAYS=0, LOG_ROTATED_MAX_BACKUPS=0)
    def test_both_zero_disables_pruning(self):
        self._make("server.log.2026_01_01__1", age_days=300)

        self.assertEqual(self.logger.prune_rotated_logs(force=True), 0)
        self.assertEqual(self._names(), ["server.log.2026_01_01__1"])

    @override_settings(
        LOG_ROTATED_RETENTION_DAYS=14,
        LOG_ROTATED_MAX_BACKUPS=0,
        LOG_ROTATED_PRUNE_MIN_INTERVAL=3600,
    )
    def test_min_interval_throttles_unforced_runs(self):
        self._make("server.log.2026_05_01__1", age_days=20)
        self.assertEqual(self.logger.prune_rotated_logs(force=True), 1)

        self._make("server.log.2026_05_02__1", age_days=20)
        # within the interval: unforced call is a no-op, forced call prunes
        self.assertEqual(self.logger.prune_rotated_logs(), 0)
        self.assertEqual(self._names(), ["server.log.2026_05_02__1"])
        self.assertEqual(self.logger.prune_rotated_logs(force=True), 1)
        self.assertEqual(self._names(), [])

    @override_settings(LOG_ROTATED_RETENTION_DAYS=14, LOG_ROTATED_MAX_BACKUPS=0)
    def test_file_vanishing_mid_scan_is_skipped(self):
        import os
        from unittest import mock

        self._make("server.log.2026_05_01__1", age_days=20)
        gone = self._make("server.log.2026_05_02__1", age_days=20)

        real_getmtime = os.path.getmtime

        def racy_getmtime(path):
            # simulate the portal process deleting the file between
            # listdir and the stat (both processes prune at startup)
            if path == gone:
                raise OSError("vanished")
            return real_getmtime(path)

        with mock.patch.object(self.logger.os.path, "getmtime", racy_getmtime):
            deleted = self.logger.prune_rotated_logs(force=True)

        self.assertEqual(deleted, 1)
        self.assertIn("server.log.2026_05_02__1", self._names())
