"""The launcher status push, on the transport that lacked it.

`ipc_handlers_portal` pushes status to the launcher after a PSYNC by calling
`send_Status2Launcher` on whatever link it was handed. Only the AMP protocol
had that method, so under the Redis bus every restart raised AttributeError,
was swallowed as "PSYNC status push failed", and the launcher never learned the
server had come back -- it waited for a status that could not arrive and timed
out its graceful shutdown.

These do not need Redis. The bus object is constructed without connecting,
because the thing under test is what it answers, not what it transports.
"""

import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from evennia.server.portal.amp_server import build_status


def _portal(*, running=True, server_pid=4321, shutdown=False):
    """A portal stand-in carrying only what the status tuple reads."""

    return SimpleNamespace(
        running=running,
        shutdown_complete=shutdown,
        server_process_id=server_pid,
        server_info_dict={"server": True},
        get_info_dict=lambda: {"portal": True},
    )


class TestStatusBuilder(TestCase):
    """Both transports compute the same tuple from the same portal."""

    def test_it_reports_the_pids_and_the_dicts(self):
        status = build_status(_portal(), True)
        self.assertEqual(status[2], os.getpid())
        self.assertEqual(status[3], 4321)
        self.assertEqual(status[4], {"portal": True})
        self.assertEqual(status[5], {"server": True})

    def test_server_liveness_is_the_caller_s_answer(self):
        # The one thing the two transports disagree about, which is why it is
        # an argument rather than something this function works out.
        self.assertTrue(build_status(_portal(), True)[1])
        self.assertFalse(build_status(_portal(), False)[1])

    def test_a_portal_shutting_down_is_not_live(self):
        self.assertFalse(build_status(_portal(shutdown=True), True)[0])

    def test_a_portal_answering_the_launcher_early_is_live(self):
        portal = _portal(running=False)
        portal._launcher_ipc_ready = True
        self.assertTrue(build_status(portal, False)[0])


class TestRedisBusAnswersTheLauncher(TestCase):
    """The regression: the method exists and pushes."""

    def _bus(self, portal, launcher):
        from evennia.server import redis_bus

        bus = redis_bus.RedisPortalBus.__new__(redis_bus.RedisPortalBus)
        bus.factory = SimpleNamespace(portal=portal, launcher_connection=launcher)
        return bus

    def test_the_method_exists_at_all(self):
        from evennia.server.redis_bus import RedisPortalBus

        self.assertTrue(hasattr(RedisPortalBus, "send_Status2Launcher"))

    def test_it_reads_liveness_from_the_server_process(self):
        # No socket to the server on this transport, so the process is the
        # only honest answer.
        portal = _portal(server_pid=os.getpid())
        with patch("evennia.server.redis_bus._pid_alive", return_value=True) as alive:
            status = self._bus(portal, None).get_status()
        self.assertTrue(alive.called)
        self.assertTrue(status[1])

    def test_a_dead_server_process_reads_as_not_connected(self):
        with patch("evennia.server.redis_bus._pid_alive", return_value=False):
            self.assertFalse(self._bus(_portal(), None).get_status()[1])

    def test_it_pushes_to_a_listening_launcher(self):
        pushed = []
        launcher = SimpleNamespace(push_status=pushed.append)
        with patch("evennia.server.redis_bus._pid_alive", return_value=True):
            self._bus(_portal(), launcher).send_Status2Launcher()
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0][3], 4321)

    def test_no_launcher_is_not_an_error(self):
        with patch("evennia.server.redis_bus._pid_alive", return_value=True):
            self._bus(_portal(), None).send_Status2Launcher()

    def test_a_launcher_that_cannot_take_a_push_is_not_an_error(self):
        # The AMP path also handles callRemote; a connection with neither is
        # simply skipped rather than raising into the PSYNC handler.
        with patch("evennia.server.redis_bus._pid_alive", return_value=True):
            self._bus(_portal(), SimpleNamespace()).send_Status2Launcher()
