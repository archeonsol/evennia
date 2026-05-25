"""
Tests for Underspire attribute fork: typed columns, write-behind, Redis L2.
"""

from unittest.mock import MagicMock

from django.test import override_settings
from mock import patch

from evennia.typeclasses.attributes import (
    Attribute,
    ModelAttributeBackend,
    _classify_value,
    _mark_attr_dirty,
    flush_all_dirty,
)
from evennia.utils.test_resources import BaseEvenniaTest


class TestClassifyValue(BaseEvenniaTest):
    def test_int_bool_float_str_none(self):
        self.assertEqual(_classify_value(7)[0], "int")
        self.assertEqual(_classify_value(True)[0], "bool")
        self.assertEqual(_classify_value(1.5)[0], "float")
        self.assertEqual(_classify_value("x")[0], "str")
        self.assertEqual(_classify_value(None)[0], "none")

    def test_pickle_path_for_dict(self):
        self.assertEqual(_classify_value({"a": 1})[0], "")


class TestWriteBehindFlush(BaseEvenniaTest):
    def test_flush_persists_dirty_attr(self):
        self.obj1.attributes.add("wb_hp", 10)
        attr = self.obj1.attributes.get("wb_hp", return_obj=True)
        attr.value = 25
        stats = flush_all_dirty()
        self.assertGreaterEqual(stats["total"], 1)
        attr.refresh_from_db()
        self.assertEqual(attr.value, 25)

    def test_orphan_dirty_flush(self):
        self.obj1.attributes.add("wb_orphan", 1)
        attr = Attribute.objects.filter(
            db_key="wb_orphan", db_model__iexact="objectdb"
        ).first()
        self.assertIsNotNone(attr)
        attr.value = 99
        _mark_attr_dirty(attr)
        stats = flush_all_dirty()
        self.assertGreaterEqual(stats["orphans"], 1)
        attr.refresh_from_db()
        self.assertEqual(attr.value, 99)

    def test_typed_column_on_add(self):
        self.obj1.attributes.add("wb_count", 42)
        attr = Attribute.objects.get(db_key="wb_count")
        self.assertEqual(attr.db_val_type, "int")
        self.assertEqual(attr.db_int_val, 42)
        self.assertEqual(attr.value, 42)


class TestPrometheusMetrics(BaseEvenniaTest):
    def test_record_attribute_flush_with_mock_counter(self):
        from evennia.server import prometheus_metrics

        mock_counter = MagicMock()
        mock_gauge = MagicMock()
        mock_histogram = MagicMock()
        with patch.object(prometheus_metrics, "_METRICS_READY", False):
            with patch.object(prometheus_metrics, "ATTR_FLUSH_TOTAL", mock_counter):
                with patch.object(prometheus_metrics, "ATTR_FLUSH_BACKENDS_TOTAL", mock_counter):
                    with patch.object(prometheus_metrics, "ATTR_FLUSH_ORPHANTS_TOTAL", mock_counter):
                        with patch.object(prometheus_metrics, "ATTR_DIRTY_PENDING", mock_gauge):
                            with patch.object(
                                prometheus_metrics, "ATTR_FLUSH_DURATION_SECONDS", mock_histogram
                            ):
                                with patch.object(prometheus_metrics, "_init_metrics", return_value=True):
                                    prometheus_metrics.record_attribute_flush(
                                        {
                                            "total": 3,
                                            "backends": 2,
                                            "orphans": 1,
                                            "pending": 5,
                                        },
                                        duration_seconds=0.01,
                                    )
        mock_gauge.set.assert_called_with(5)
        self.assertTrue(mock_counter.inc.called)
        mock_histogram.observe.assert_called_with(0.01)

    def test_count_pending_dirty(self):
        from evennia.typeclasses.attributes import count_pending_dirty

        self.obj1.attributes.add("pending_hp", 1)
        attr = self.obj1.attributes.get("pending_hp", return_obj=True)
        attr.value = 2
        pending = count_pending_dirty()
        self.assertGreaterEqual(pending["pending"], 1)


class TestRedisAttrCache(BaseEvenniaTest):
    def test_encode_hydrate_roundtrip_without_pg(self):
        from evennia.typeclasses import redis_attr_cache

        attr = Attribute(
            pk=999,
            db_key="redis_hp",
            db_category=None,
            db_model="objectdb",
            db_attrtype=None,
            db_lock_storage="",
            db_val_type="int",
            db_int_val=100,
            db_float_val=None,
            db_str_val=None,
            db_strvalue=None,
            db_value=None,
        )
        raw = redis_attr_cache._encode_attr(attr)
        with patch.object(Attribute.objects, "get") as pg_get:
            hydrated = redis_attr_cache._hydrate_attr_from_payload(Attribute, raw)
            pg_get.assert_not_called()
        self.assertIsNotNone(hydrated)
        self.assertEqual(hydrated.value, 100)

    @override_settings(
        ATTRIBUTE_REDIS_CACHE_ENABLED=True,
        ATTRIBUTE_BACKEND_CLASS=(
            "evennia.typeclasses.redis_attr_cache.RedisCachedModelAttributeBackend"
        ),
    )
    def test_query_key_hydrates_without_pg_get(self):
        from evennia.typeclasses import redis_attr_cache

        store = {}

        class FakeRedis:
            def get(self, key):
                return store.get(key)

            def set(self, key, val, ex=None):
                store[key] = val

            def sadd(self, key, member):
                store.setdefault(key, set()).add(member)

            def expire(self, key, ttl):
                pass

            def pipeline(self):
                p = MagicMock()

                def _set(key, val, ex=None):
                    store[key] = val

                def _sadd(key, member):
                    store.setdefault(key, set()).add(member)

                p.set = _set
                p.sadd = _sadd
                p.expire = lambda *a, **k: None
                p.execute = lambda: None
                return p

            def exists(self, key):
                return 1 if _obj_index_key in store else 0

            def smembers(self, key):
                return store.get(key, set())

        _obj_index_key = redis_attr_cache._obj_index_key("objectdb", self.obj1.id)

        self.obj1.attributes.add("redis_hp", 100)
        attr = Attribute.objects.get(db_key="redis_hp")
        payload = redis_attr_cache._encode_attr(attr)
        rkey = redis_attr_cache._redis_key("objectdb", self.obj1.id, "redis_hp", None)

        fake = FakeRedis()
        fake.set(rkey, payload)
        fake.sadd(_obj_index_key, rkey)

        if "attributes" in self.obj1.__dict__:
            del self.obj1.__dict__["attributes"]

        with patch.object(redis_attr_cache, "_redis_conn", return_value=fake):
            with patch.object(Attribute.objects, "get") as pg_get:
                backend = self.obj1.attributes.backend
                conns = backend.query_key("redis_hp", None)
                pg_get.assert_not_called()
                self.assertEqual(len(conns), 1)
                self.assertEqual(conns[0].attribute.value, 100)
