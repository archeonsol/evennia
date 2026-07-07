"""Tests for asyncio service registry (T3 phase 2)."""

from django.test import SimpleTestCase

from evennia.server.service_registry import IMMEDIATE_RESULT, MultiService, Service


class _ChildService(Service):
    started = False
    stopped = False

    def _on_start(self):
        self.started = True

    def _on_stop(self):
        self.stopped = True


class _PrivilegedRoot(MultiService):
    privileged_count = 0

    def _privileged_start(self):
        self.privileged_count += 1


class ServiceRegistryTest(SimpleTestCase):
    def test_privileged_start_runs_once(self):
        root = _PrivilegedRoot()
        root.privilegedStartService()
        root.startService()
        self.assertEqual(root.privileged_count, 1)

    def test_child_start_stop_order(self):
        root = MultiService()
        child_a = _ChildService()
        child_b = _ChildService()
        root.addService(child_a)
        root.addService(child_b)

        root.startService()
        self.assertTrue(root.running)
        self.assertTrue(child_a.started)
        self.assertTrue(child_b.started)

        root.stopService()
        self.assertFalse(root.running)
        self.assertTrue(child_a.stopped)
        self.assertTrue(child_b.stopped)

    def test_immediate_result_errback_chain(self):
        called = []
        IMMEDIATE_RESULT.addErrback(lambda *a: called.append(a))
        self.assertEqual(called, [])
