"""
Test the evennia launcher.

"""

import os
import shutil
import tempfile
import threading
import time
import unittest

import psutil
from anything import Something
from django.test.utils import override_settings
from mock import MagicMock, create_autospec, patch
from twisted.internet.base import DelayedCall
from twisted.trial.unittest import TestCase as TwistedTestCase

from evennia.server import evennia_launcher
from evennia.server.amp_serde import pack_status, unpack_status
from evennia.server.portal import amp

DelayedCall.debug = True


@patch.object(evennia_launcher, "Popen", new=MagicMock())
class TestLauncher(TwistedTestCase):
    def tearDown(self):
        # some tests toggle launcher globals; reset so they never leak between tests
        evennia_launcher.AMP_CONNECTION = None
        evennia_launcher.REACTOR_RUN = False
        super().tearDown()

    def test_is_windows(self):
        self.assertEqual(evennia_launcher._is_windows(), os.name == "nt")

    def test_file_compact(self):
        self.assertEqual(
            evennia_launcher._file_names_compact("foo/bar/test1", "foo/bar/test2"),
            "foo/bar/test1 and test2",
        )

        self.assertEqual(
            evennia_launcher._file_names_compact("foo/test1", "foo/bar/test2"),
            "foo/test1 and foo/bar/test2",
        )

    @patch("evennia.server.evennia_launcher.print")
    def test_print_info(self, mockprint):
        portal_dict = {
            "servername": "testserver",
            "version": "1",
            "telnet": 1234,
            "telnet_ssl": [1234, 2345],
            "ssh": 1234,
            "webserver_proxy": 1234,
            "webclient": 1234,
            "webserver_internal": 1234,
            "amp": 1234,
        }
        server_dict = {
            "servername": "testserver",
            "version": "1",
            "webserver": [1234, 1234],
            "amp": 1234,
            "irc_rss": "irc.test",
            "info": "testing mode",
            "errors": "",
        }

        evennia_launcher._print_info(portal_dict, server_dict)
        mockprint.assert_called()

    def test_parse_status(self):
        # JSON does not preserve tuples; the status round-trips as a list.
        response = {"status": pack_status(("teststring",))}
        result = evennia_launcher._parse_status(response)
        self.assertEqual(result, ["teststring"])

    @override_settings(EVENNIA_ASYNCIO_BOOTSTRAP=False)
    @patch("evennia.server.evennia_launcher.os.name", new="posix")
    @patch("evennia.server.evennia_launcher.PORTAL_PY_FILE", "/p/portal.py")
    @patch("evennia.server.evennia_launcher.SERVER_PY_FILE", "/p/server.py")
    @patch("evennia.server.evennia_launcher.PORTAL_PIDFILE", "/game/portal.pid")
    @patch("evennia.server.evennia_launcher.SERVER_PIDFILE", "/game/server.pid")
    def test_get_twisted_cmdline(self):
        pcmd, scmd = evennia_launcher._get_twistd_cmdline(False, False)
        self.assertTrue(any("portal.py" in arg for arg in pcmd))
        self.assertTrue(any("server.py" in arg for arg in scmd))
        self.assertTrue(any("--pidfile" in arg for arg in pcmd))
        self.assertTrue(any("--pidfile" in arg for arg in scmd))

        pcmd, scmd = evennia_launcher._get_twistd_cmdline(True, True)
        self.assertTrue(any("portal.py" in arg for arg in pcmd))
        self.assertTrue(any("--profiler=cprofile" in arg for arg in pcmd))
        self.assertTrue(any(arg.startswith("--profile=") for arg in pcmd))
        self.assertTrue(any("server.py" in arg for arg in scmd))
        self.assertTrue(any("--profiler=cprofile" in arg for arg in scmd))
        self.assertTrue(any(arg.startswith("--profile=") for arg in scmd))

    @override_settings(EVENNIA_ASYNCIO_BOOTSTRAP=False)
    @patch("evennia.server.evennia_launcher.os.name", new="nt")
    @patch("evennia.server.evennia_launcher.PORTAL_PY_FILE", "/p/portal.py")
    @patch("evennia.server.evennia_launcher.SERVER_PY_FILE", "/p/server.py")
    def test_get_twisted_cmdline_nt(self):
        pcmd, scmd = evennia_launcher._get_twistd_cmdline(False, False)
        # on Windows no --pidfile is appended, so both are just [python, py_file]
        self.assertTrue(len(pcmd) == 2, pcmd)
        self.assertTrue(len(scmd) == 2, scmd)

    def test_reactor_stop(self):
        evennia_launcher.REACTOR_RUN = True
        evennia_launcher._reactor_stop()
        self.assertFalse(evennia_launcher.REACTOR_RUN)

    def _catch_wire_read(self, mocktransport):
        "Parse what was supposed to be sent over the wire"
        arg_list = mocktransport.write.call_args_list

        all_sent = []
        for i, cll in enumerate(arg_list):
            args, kwargs = cll
            raw_inp = args[0]
            all_sent.append(raw_inp)

        return all_sent

    # @patch("evennia.server.portal.amp.amp.BinaryBoxProtocol.transport")
    # def test_send_instruction_pstatus(self, mocktransport):

    #     deferred = evennia_launcher.send_instruction(
    #         evennia_launcher.PSTATUS,
    #         (),
    #         callback=MagicMock(),
    #         errback=MagicMock())

    #     on_wire = self._catch_wire_read(mocktransport)
    #     self.assertEqual(on_wire, "")

    #     return deferred

    def _msend_status_ok(operation, arguments, callback=None, errback=None):
        callback({"status": pack_status((True, True, 2, 24, "info1", "info2"))})

    def _msend_status_err(operation, arguments, callback=None, errback=None):
        errback({"status": pack_status((False, False, 3, 25, "info3", "info4"))})

    @patch.object(evennia_launcher, "send_instruction", _msend_status_ok)
    @patch.object(evennia_launcher, "NO_REACTOR_STOP", True)
    @patch.object(evennia_launcher, "get_pid", MagicMock(return_value=100))
    @patch("evennia.server.evennia_launcher.print")
    def test_query_status_run(self, mprint):
        evennia_launcher.query_status()
        mprint.assert_called_with("Portal: RUNNING (pid 100)\nServer: RUNNING (pid 100)")

    @patch.object(evennia_launcher, "send_instruction", _msend_status_err)
    @patch.object(evennia_launcher, "NO_REACTOR_STOP", True)
    @patch("evennia.server.evennia_launcher.print")
    def test_query_status_not_run(self, mprint):
        evennia_launcher.query_status()
        mprint.assert_called_with("Portal: NOT RUNNING\nServer: NOT RUNNING")

    @patch.object(evennia_launcher, "send_instruction", _msend_status_ok)
    @patch.object(evennia_launcher, "NO_REACTOR_STOP", True)
    def test_query_status_callback(self):
        mprint = MagicMock()

        def testcall(response):
            resp = unpack_status(response["status"])
            mprint(resp)

        evennia_launcher.query_status(callback=testcall)
        mprint.assert_called_with([True, True, 2, 24, "info1", "info2"])

    def _run_status_reply(self, push_value, mcall, on_fail):
        """
        Drive wait_for_status_reply against a stub IPC session and join its
        reader thread deterministically (no socket, no leaked thread).
        """
        from evennia.server import launcher_ipc

        session = create_autospec(launcher_ipc.LauncherSession, instance=True)
        session.wait_for_push.return_value = push_value

        created = []
        real_thread = threading.Thread

        def _capture_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            created.append(thread)
            return thread

        with (
            patch.object(evennia_launcher, "_ensure_ipc_connection", return_value=session),
            patch.object(evennia_launcher, "_reactor_stop") as mstop,
            patch.object(evennia_launcher.threading, "Thread", side_effect=_capture_thread),
        ):
            evennia_launcher.wait_for_status_reply(mcall, on_fail=on_fail)
            self.assertEqual(len(created), 1)
            created[0].join(timeout=5)
            self.assertFalse(created[0].is_alive())

        return mstop

    def test_wait_for_status_reply(self):
        status = (True, True, 2, 24, "info1", "info2")
        mcall = MagicMock()
        on_fail = MagicMock()

        mstop = self._run_status_reply(status, mcall, on_fail)

        mcall.assert_called_once_with(status)
        on_fail.assert_not_called()
        mstop.assert_not_called()

    def test_wait_for_status_reply_fail(self):
        mcall = MagicMock()
        on_fail = MagicMock()

        mstop = self._run_status_reply(None, mcall, on_fail)

        mcall.assert_not_called()
        on_fail.assert_called_once_with()
        mstop.assert_called_once_with()

    def test_wait_for_status(self):
        mcall = MagicMock()
        merr = MagicMock()

        def _stub(portal_running, server_running, callback, errback, rate=0.5, retries=None):
            callback(portal_running, server_running)

        with patch.object(evennia_launcher, "_wait_for_status_ipc", side_effect=_stub):
            evennia_launcher.wait_for_status(
                portal_running=True, server_running=True, callback=mcall, errback=merr
            )

        mcall.assert_called_once_with(True, True)
        merr.assert_not_called()

    def test_wait_for_status_fail(self):
        mcall = MagicMock()
        merr = MagicMock()

        def _stub(portal_running, server_running, callback, errback, rate=0.5, retries=None):
            errback(portal_running, server_running)

        with patch.object(evennia_launcher, "_wait_for_status_ipc", side_effect=_stub):
            evennia_launcher.wait_for_status(
                portal_running=True, server_running=True, callback=mcall, errback=merr
            )

        mcall.assert_not_called()
        merr.assert_called_once_with(True, True)


class TestLauncherIPCConnection(unittest.TestCase):
    """The shared launcher IPC connection must be created and mutated safely."""

    def setUp(self):
        self._saved = evennia_launcher.AMP_CONNECTION
        evennia_launcher.AMP_CONNECTION = None

    def tearDown(self):
        evennia_launcher.AMP_CONNECTION = self._saved

    def test_ensure_ipc_connection_is_thread_safe(self):
        from evennia.server import launcher_ipc

        calls = []

        def _slow_connect(*args, **kwargs):
            # count the connect and hold long enough that a racing thread would
            # also pass the None check before this one assigns AMP_CONNECTION
            calls.append(1)
            time.sleep(0.1)
            return create_autospec(launcher_ipc.LauncherSession, instance=True)

        with patch.object(launcher_ipc, "connect_session", side_effect=_slow_connect):
            threads = [
                threading.Thread(target=evennia_launcher._ensure_ipc_connection) for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # both threads shared one connection instead of each opening its own
        self.assertEqual(len(calls), 1)

    def test_send_instruction_ipc_surfaces_failure_without_errback(self):
        from evennia.server import launcher_ipc

        session = create_autospec(launcher_ipc.LauncherSession, instance=True)
        session.send_command_fire.side_effect = RuntimeError("boom")
        evennia_launcher.AMP_CONNECTION = session

        with patch("traceback.print_exc") as mock_print_exc:
            evennia_launcher._send_instruction_ipc(evennia_launcher.SRELOAD, {})

        # the failure is surfaced (not silently swallowed) and the stale
        # connection is cleared for the next attempt
        self.assertTrue(mock_print_exc.called)
        self.assertIsNone(evennia_launcher.AMP_CONNECTION)


class TestForceKillIdentity(unittest.TestCase):
    """Force-kill must confirm a pid is still ours before signalling it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.server_pid = os.path.join(self.tmp, "server.pid")
        self.portal_pid = os.path.join(self.tmp, "portal.pid")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        evennia_launcher.LAUNCHER_FAILED = False

    def _write(self, path, pid):
        with open(path, "w") as fh:
            fh.write(str(pid))

    @patch("evennia.server.evennia_launcher.wait_for_portal_ipc_down", create=True)
    @patch("evennia.server.evennia_launcher.psutil.wait_procs", return_value=([], []))
    def test_force_kill_marks_launcher_failed(self, _mock_wait, _mock_ipc):
        # a force-kill fallback means the graceful stop did not work -> exit non-zero
        evennia_launcher.LAUNCHER_FAILED = False
        with (
            patch.object(evennia_launcher, "SERVER_PIDFILE", None),
            patch.object(evennia_launcher, "PORTAL_PIDFILE", None),
            patch.object(evennia_launcher, "AMP_HOST", None),
        ):
            evennia_launcher._force_kill_local_processes()
        self.assertTrue(evennia_launcher.LAUNCHER_FAILED)

    @patch("evennia.server.evennia_launcher.psutil.Process")
    def test_our_process_matches_by_cmdline(self, mock_proc):
        self._write(self.server_pid, 4242)
        mock_proc.return_value.cmdline.return_value = ["/py", "/lib/server.py", "--pidfile", "x"]
        proc = evennia_launcher._our_process(self.server_pid, "/lib/server.py")
        self.assertIs(proc, mock_proc.return_value)
        mock_proc.assert_called_once_with(4242)

    @patch("evennia.server.evennia_launcher.psutil.Process")
    def test_our_process_rejects_recycled_pid(self, mock_proc):
        self._write(self.server_pid, 4242)
        mock_proc.return_value.cmdline.return_value = ["/usr/bin/vim", "notes.txt"]
        self.assertIsNone(evennia_launcher._our_process(self.server_pid, "/lib/server.py"))

    @patch("evennia.server.evennia_launcher.psutil.Process")
    def test_our_process_missing(self, mock_proc):
        self._write(self.server_pid, 4242)
        mock_proc.side_effect = psutil.NoSuchProcess(4242)
        self.assertIsNone(evennia_launcher._our_process(self.server_pid, "/lib/server.py"))

    @patch("evennia.server.evennia_launcher.wait_for_portal_ipc_down", create=True)
    @patch("evennia.server.evennia_launcher.psutil.Process")
    def test_force_kill_skips_recycled_via_cmdline(self, mock_proc, _mock_ipc):
        # pid 4242 was recycled to an unrelated process; it must not be signalled
        self._write(self.server_pid, 4242)
        inst = mock_proc.return_value
        inst.cmdline.return_value = ["/usr/bin/vim", "notes.txt"]
        with (
            patch.object(evennia_launcher, "SERVER_PIDFILE", self.server_pid),
            patch.object(evennia_launcher, "PORTAL_PIDFILE", None),
            patch.object(evennia_launcher, "SERVER_PY_FILE", "/lib/server.py"),
            patch.object(evennia_launcher, "AMP_HOST", None),
        ):
            evennia_launcher._force_kill_local_processes()
        inst.terminate.assert_not_called()
        inst.kill.assert_not_called()
        self.assertFalse(os.path.isfile(self.server_pid))  # stale pidfile cleaned

    @patch("evennia.server.evennia_launcher.wait_for_portal_ipc_down", create=True)
    def test_force_kill_terminates_our_process(self, _mock_ipc):
        self._write(self.server_pid, 100)
        proc = MagicMock()
        proc.is_running.return_value = False  # dies on SIGTERM

        def _our(pidfile, py_file):
            return proc if pidfile == self.server_pid else None

        with (
            patch.object(evennia_launcher, "SERVER_PIDFILE", self.server_pid),
            patch.object(evennia_launcher, "PORTAL_PIDFILE", None),
            patch.object(evennia_launcher, "AMP_HOST", None),
            patch("evennia.server.evennia_launcher._our_process", side_effect=_our),
            patch("evennia.server.evennia_launcher.psutil.wait_procs", return_value=([proc], [])),
        ):
            evennia_launcher._force_kill_local_processes()
        proc.terminate.assert_called_once()
        proc.kill.assert_not_called()  # died on SIGTERM, no SIGKILL needed
        self.assertFalse(os.path.isfile(self.server_pid))  # removed after death

    @patch("evennia.server.evennia_launcher.wait_for_portal_ipc_down", create=True)
    def test_force_kill_sigkills_survivor(self, _mock_ipc):
        self._write(self.server_pid, 100)
        proc = MagicMock()
        proc.is_running.return_value = False  # gone after SIGKILL

        def _our(pidfile, py_file):
            return proc if pidfile == self.server_pid else None

        with (
            patch.object(evennia_launcher, "SERVER_PIDFILE", self.server_pid),
            patch.object(evennia_launcher, "PORTAL_PIDFILE", None),
            patch.object(evennia_launcher, "AMP_HOST", None),
            patch("evennia.server.evennia_launcher._our_process", side_effect=_our),
            patch("evennia.server.evennia_launcher.psutil.wait_procs", return_value=([], [proc])),
        ):
            evennia_launcher._force_kill_local_processes()
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()  # survived SIGTERM, escalated to SIGKILL
