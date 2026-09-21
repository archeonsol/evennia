"""The six settings-backed bus caps reach the resolved limits object."""

from django.test import SimpleTestCase, override_settings

from evennia.server import redis_transport


class TestBusLimitsResolution(SimpleTestCase):
    def test_settings_override_module_defaults(self):
        with override_settings(
            REDIS_BUS_MAX_ENTRIES=9,
            REDIS_BUS_MAX_BYTES=8000,
            REDIS_BUS_DATA_ENTRIES=7,
            REDIS_BUS_DATA_BYTES=6000,
            REDIS_BUS_WRITE_BATCH=5,
            REDIS_BUS_READ_BATCH=4,
        ):
            limits = redis_transport.resolve_limits()
        self.assertEqual(limits.max_entries, 9)
        self.assertEqual(limits.max_bytes, 8000)
        self.assertEqual(limits.data_entries, 7)
        self.assertEqual(limits.data_bytes, 6000)
        self.assertEqual(limits.write_batch, 5)
        self.assertEqual(limits.read_batch, 4)

    def test_unset_settings_keep_module_constants(self):
        with override_settings(
            REDIS_BUS_MAX_ENTRIES=None,
            REDIS_BUS_MAX_BYTES=None,
            REDIS_BUS_DATA_ENTRIES=None,
            REDIS_BUS_DATA_BYTES=None,
            REDIS_BUS_WRITE_BATCH=None,
            REDIS_BUS_READ_BATCH=None,
        ):
            limits = redis_transport.resolve_limits()
        self.assertEqual(limits.max_entries, redis_transport.MAX_ENTRIES)
        self.assertEqual(limits.max_bytes, redis_transport.MAX_BYTES)
        self.assertEqual(limits.data_entries, redis_transport.DATA_ENTRIES)
        self.assertEqual(limits.data_bytes, redis_transport.DATA_BYTES)
        self.assertEqual(limits.write_batch, redis_transport.WRITE_BATCH_SIZE)
        self.assertEqual(limits.read_batch, redis_transport.READ_BATCH)

    def test_non_positive_values_floor_at_one(self):
        with override_settings(REDIS_BUS_MAX_ENTRIES=0, REDIS_BUS_READ_BATCH=-5):
            limits = redis_transport.resolve_limits()
        self.assertEqual(limits.max_entries, 1)
        self.assertEqual(limits.read_batch, 1)
