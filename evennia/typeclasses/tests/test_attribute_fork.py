"""
Tests for Underspire attribute fork: typed columns, write-behind, Redis L2.
"""

from unittest.mock import MagicMock

from django.test import override_settings
from mock import patch

from evennia.typeclasses.attributes import (Attribute, ModelAttributeBackend,
                                            _classify_value, _mark_attr_dirty,
                                            flush_all_dirty)
from evennia.utils.test_resources import BaseEvenniaTest


class TestClassifyValue(BaseEvenniaTest):
    def test_int_bool_float_str_none(self):
        self.assertEqual(_classify_value(7)[0], "int")
        self.assertEqual(_classify_value(True)[0], "bool")
        self.assertEqual(_classify_value(1.5)[0], "float")
        self.assertEqual(_classify_value("x")[0], "str")
        self.assertEqual(_classify_value(None)[0], "none")

    def test_json_path_for_plain_dict(self):
        # JSON-safe dicts/lists go through the json typed column.
        self.assertEqual(_classify_value({"a": 1})[0], "json")
        self.assertEqual(_classify_value([1, 2, 3])[0], "json")

    def test_pickle_path_for_tuple_and_unsafe_containers(self):
        # Tuples have no JSON type and would silently demote to lists, so
        # both standalone tuples and structures containing them must take
        # the pickle path to preserve type fidelity.
        self.assertEqual(_classify_value((1, 2))[0], "")
        self.assertEqual(_classify_value({"a": (1, 2)})[0], "")
        self.assertEqual(_classify_value([1, (2, 3)])[0], "")
        # Sets aren't JSON-representable either.
        self.assertEqual(_classify_value({1, 2, 3})[0], "")

    def test_pickle_path_for_oversized_int(self):
        # Python ints > 2^63 - 1 don't fit BigIntegerField and must
        # route to pickle instead of crashing the INSERT.
        self.assertEqual(_classify_value(1 << 63)[0], "")
        self.assertEqual(_classify_value(-(1 << 63) - 1)[0], "")
        # Boundary values still fit.
        self.assertEqual(_classify_value((1 << 63) - 1)[0], "int")
        self.assertEqual(_classify_value(-(1 << 63))[0], "int")


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
        attr = Attribute.objects.filter(db_key="wb_orphan", db_model__iexact="objectdb").first()
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
                    with patch.object(
                        prometheus_metrics, "ATTR_FLUSH_ORPHANTS_TOTAL", mock_counter
                    ):
                        with patch.object(prometheus_metrics, "ATTR_DIRTY_PENDING", mock_gauge):
                            with patch.object(
                                prometheus_metrics, "ATTR_FLUSH_DURATION_SECONDS", mock_histogram
                            ):
                                with patch.object(
                                    prometheus_metrics, "_init_metrics", return_value=True
                                ):
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

    @override_settings(ATTRIBUTE_REDIS_CACHE_ENABLED=True)
    def test_idmapper_flush_cache_drops_redis_keys(self):
        # Wired in +underspire.6: flush_cache() must clear the Redis L2
        # cache, otherwise CI runs with persistent Redis see stale
        # Attribute rows leaking from a prior test's writes.
        from evennia.typeclasses import redis_attr_cache
        from evennia.utils.idmapper.models import flush_cache

        deleted = []
        scanned = []

        class FakeRedis:
            def scan_iter(self, match=None, count=None):
                scanned.append((match, count))
                # Yield two fake matching keys to confirm batching.
                yield b"attr:v2:objectdb:1:hp:"
                yield b"attr:v2:objectdb:1:__index__"

            def delete(self, *keys):
                deleted.extend(keys)
                return len(keys)

        with patch.object(redis_attr_cache, "_redis_conn", return_value=FakeRedis()):
            flush_cache()

        self.assertEqual(len(scanned), 1)
        self.assertEqual(scanned[0][0], "attr:v2:*")
        self.assertEqual(
            deleted,
            [b"attr:v2:objectdb:1:hp:", b"attr:v2:objectdb:1:__index__"],
        )

    @override_settings(ATTRIBUTE_REDIS_CACHE_ENABLED=False)
    def test_flush_all_keys_noop_when_disabled(self):
        from evennia.typeclasses import redis_attr_cache

        # Should not even try to open a connection when disabled.
        with patch.object(redis_attr_cache, "_redis_conn") as conn:
            self.assertEqual(redis_attr_cache.flush_all_keys(), 0)
            conn.assert_not_called()

    @override_settings(ATTRIBUTE_REDIS_CACHE_ENABLED=True)
    def test_flush_all_keys_noop_when_redis_unavailable(self):
        from evennia.typeclasses import redis_attr_cache

        with patch.object(redis_attr_cache, "_redis_conn", return_value=None):
            self.assertEqual(redis_attr_cache.flush_all_keys(), 0)

    @override_settings(
        ATTRIBUTE_REDIS_CACHE_ENABLED=True,
        ATTRIBUTE_BACKEND_CLASS=(
            "evennia.typeclasses.redis_attr_cache.RedisCachedModelAttributeBackend"
        ),
    )
    def test_update_does_not_publish_redis_before_flush(self):
        # Invariant: Redis is never more current than PG. ``do_update_attribute``
        # marks the attr dirty without touching Redis; the publish happens in
        # ``flush_dirty`` after ``bulk_update`` succeeds. This prevents
        # phantom-data after a crash between cache write and PG flush.
        from evennia.typeclasses import redis_attr_cache
        from evennia.typeclasses.attributes import flush_all_dirty

        calls = []

        class FakeRedis:
            def get(self, key):
                return None

            def set(self, *a, **k):
                calls.append(("set", a, k))

            def sadd(self, *a, **k):
                calls.append(("sadd", a, k))

            def srem(self, *a, **k):
                calls.append(("srem", a, k))

            def delete(self, *a, **k):
                calls.append(("delete", a, k))

            def expire(self, *a, **k):
                pass

            def exists(self, key):
                return 0

            def smembers(self, key):
                return set()

            def pipeline(self, transaction=False):
                pipe_calls = calls

                class _Pipe:
                    def set(self, *a, **k):
                        pipe_calls.append(("set", a, k))
                        return self

                    def sadd(self, *a, **k):
                        pipe_calls.append(("sadd", a, k))
                        return self

                    def srem(self, *a, **k):
                        pipe_calls.append(("srem", a, k))
                        return self

                    def delete(self, *a, **k):
                        pipe_calls.append(("delete", a, k))
                        return self

                    def expire(self, *a, **k):
                        return self

                    def execute(self):
                        return []

                return _Pipe()

        self.obj1.attributes.add("wb_redis", 1)
        if "attributes" in self.obj1.__dict__:
            del self.obj1.__dict__["attributes"]

        with patch.object(redis_attr_cache, "_redis_conn", return_value=FakeRedis()):
            backend = self.obj1.attributes.backend
            attr = self.obj1.attributes.get("wb_redis", return_obj=True)
            calls.clear()
            backend.update_attribute(attr, 99, False)
            self.assertEqual(calls, [], "do_update_attribute must not touch Redis pre-flush")
            flush_all_dirty()
            self.assertTrue(
                any(op == "set" for op, _, _ in calls),
                "flush_dirty should publish the new value to Redis",
            )

    @override_settings(
        ATTRIBUTE_REDIS_CACHE_ENABLED=True,
        ATTRIBUTE_BACKEND_CLASS=(
            "evennia.typeclasses.redis_attr_cache.RedisCachedModelAttributeBackend"
        ),
    )
    def test_flush_failure_does_not_publish_redis(self):
        # If bulk_update raises, the parent re-raises before Redis publish.
        # The dirty entries stay queued for retry, Redis is untouched, and
        # readers continue to see whatever PG holds.
        from evennia.typeclasses import redis_attr_cache
        from evennia.typeclasses.attributes import flush_all_dirty

        publish_calls = []

        class FakeRedis:
            def get(self, key):
                return None

            def exists(self, key):
                return 0

            def smembers(self, key):
                return set()

            def pipeline(self, transaction=False):
                outer = publish_calls

                class _Pipe:
                    def set(self, *a, **k):
                        outer.append(("set", a, k))
                        return self

                    def sadd(self, *a, **k):
                        return self

                    def expire(self, *a, **k):
                        return self

                    def execute(self):
                        return []

                return _Pipe()

        self.obj1.attributes.add("wb_fail", 1)
        if "attributes" in self.obj1.__dict__:
            del self.obj1.__dict__["attributes"]

        with patch.object(redis_attr_cache, "_redis_conn", return_value=FakeRedis()):
            backend = self.obj1.attributes.backend
            attr = self.obj1.attributes.get("wb_fail", return_obj=True)
            backend.update_attribute(attr, 7, False)
            publish_calls.clear()
            with patch.object(Attribute.objects, "bulk_update", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    flush_all_dirty()
            self.assertEqual(
                publish_calls,
                [],
                "Redis must not be published when bulk_update fails",
            )

    def test_orphan_flush_failure_keeps_attrs_queued(self):
        # If bulk_update raises during the orphan-dirty flush, the attrs
        # must remain in _ORPHAN_DIRTY_ATTRS so the next maintenance tick
        # retries. Pre-fix behaviour discarded them before bulk_update ran
        # and silently lost the writes on failure.
        from evennia.typeclasses.attributes import (_ORPHAN_DIRTY_ATTRS,
                                                    flush_all_dirty)

        self.obj1.attributes.add("orphan_retry", 1)
        attr = Attribute.objects.filter(db_key="orphan_retry", db_model__iexact="objectdb").first()
        self.assertIsNotNone(attr)
        attr.value = 99  # orphan-dirty path

        self.assertIn(attr, _ORPHAN_DIRTY_ATTRS)

        with patch.object(Attribute.objects, "bulk_update", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                flush_all_dirty()

        self.assertIn(
            attr,
            _ORPHAN_DIRTY_ATTRS,
            "Orphan-dirty attr must stay queued when bulk_update fails",
        )

    @override_settings(ATTRIBUTE_REDIS_CACHE_ENABLED=True)
    def test_orphan_flush_invalidates_redis(self):
        # Direct ``attr.value = X`` writes go through the orphan-dirty path,
        # which calls ``bulk_update`` without touching any backend. Redis
        # would otherwise serve the stale pre-write payload until TTL.
        # invalidate_attrs() must drop the affected keys after the PG write.
        from evennia.typeclasses import redis_attr_cache
        from evennia.typeclasses.attributes import flush_all_dirty

        deletes = []

        class FakeRedis:
            def delete(self, *keys):
                deletes.extend(keys)
                return len(keys)

        self.obj1.attributes.add("orphan_redis", 1)
        attr = Attribute.objects.filter(db_key="orphan_redis", db_model__iexact="objectdb").first()
        self.assertIsNotNone(attr)
        attr.value = 42  # orphan-dirty path

        expected_key = redis_attr_cache._redis_key("objectdb", self.obj1.id, "orphan_redis", None)

        with patch.object(redis_attr_cache, "_redis_conn", return_value=FakeRedis()):
            flush_all_dirty()

        self.assertIn(expected_key, deletes)
