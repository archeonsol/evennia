"""
Tests for at_init_scheduler batching.
"""

from unittest.mock import MagicMock, patch

from django.test import override_settings

from evennia.server import at_init_scheduler
from evennia.utils.test_resources import BaseEvenniaTest


def _sync_call_later(delay, fn, *a, **kw):
    """Stand-in for ``clock.call_later`` that runs synchronously.

    Django's TestCase doesn't run an event loop, so any batch that
    schedules its successor via ``clock.call_later`` would never get
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
            patch.object(
                at_init_scheduler,
                "_collect_cached_entities",
                return_value=[entity, entity, entity],
            ),
            patch("evennia.utils.clock.call_later", _sync_call_later),
        ):
            run_cached_at_init_burst("shutdown")
        self.assertEqual(entity.at_post_load.call_count, 3)
