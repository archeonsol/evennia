"""
Tests for at_init_scheduler batching.
"""

from unittest.mock import MagicMock, patch

from django.test import override_settings

from evennia.utils.test_resources import BaseEvenniaTest


def _sync_call_later(delay, fn, *a, **kw):
    """Stand-in for ``reactor.callLater`` that runs synchronously.

    Django's TestCase doesn't pump the reactor, so any batch that
    schedules its successor via ``reactor.callLater`` would never get
    its tail batch executed. Running the call synchronously preserves
    the batching contract under test.
    """
    fn(*a, **kw)
    return MagicMock()


class TestAtInitScheduler(BaseEvenniaTest):
    @override_settings(AT_INIT_BATCH_SIZE=2, AT_INIT_DEFER_ON_RELOAD=False)
    def test_run_cached_at_init_burst_calls_at_init(self):
        from evennia.server.at_init_scheduler import run_cached_at_init_burst

        entity = MagicMock()
        with (
            patch(
                "evennia.server.at_init_scheduler._collect_cached_entities",
                return_value=[entity, entity, entity],
            ),
            patch("twisted.internet.reactor.callLater", _sync_call_later),
        ):
            run_cached_at_init_burst("shutdown")
        self.assertEqual(entity.at_init.call_count, 3)
