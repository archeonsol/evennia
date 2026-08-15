"""Tests for bounded idmapper pressure sweeps."""

import concurrent.futures
from unittest.mock import PropertyMock, patch

from django.core.exceptions import FieldDoesNotExist
from django.db import DatabaseError
from django.test import SimpleTestCase

from evennia.utils.idmapper import models as idmapper


class _FakeObject:
    """Minimal cached object exposing the idmapper flush contract."""

    def __init__(self, key, *, retain=False, on_flush=None, row_check=False):
        self.pk = key
        self.retain = retain
        self.on_flush = on_flush
        self.row_check = row_check
        self.flush_calls = 0
        self.missing_calls = 0
        self._state = type("State", (), {"db": "default"})()
        self._meta = type("Meta", (), {"concrete_model": _FakeModel})()

    def at_idmapper_flush(self):
        """Record the hook and optionally retain this object."""
        self.flush_calls += 1
        if self.on_flush:
            self.on_flush()
        return not self.retain

    def _idmapper_row_check_required(self):
        """Return whether this object needs bulk row-existence validation."""
        return self.row_check

    def _idmapper_mark_row_missing(self):
        """Record fail-closed missing-row handling."""
        self.missing_calls += 1


class _FakeModel:
    """Concrete cache holder used by the bounded sweep."""

    __instance_cache__ = {}


class _FakeQueryManager:
    """Record bounded primary-key queries and report every row present."""

    def __init__(self):
        self.calls = []
        self.current = []

    def using(self, alias):
        return self

    def filter(self, **kwargs):
        self.current = list(kwargs["pk__in"])
        self.calls.append(self.current)
        return self

    def values_list(self, *args, **kwargs):
        return self.current


class TestBoundedIdmapperSweep(SimpleTestCase):
    """The automatic sweep must remain bounded and identity-safe."""

    def setUp(self):
        _FakeModel.__instance_cache__ = {}

    def _sweep(self, *, batch_size=2, budget_ms=1000):
        return idmapper._CacheFlushSweep(
            models=[_FakeModel],
            batch_size=batch_size,
            budget_ms=budget_ms,
            automatic=True,
        )

    def test_turn_processes_only_batch_and_rotates_retained_entries(self):
        objects = [_FakeObject(key, retain=True) for key in range(1, 6)]
        _FakeModel.__instance_cache__ = {obj.pk: obj for obj in objects}
        sweep = self._sweep(batch_size=2)

        self.assertTrue(sweep.run_turn())

        self.assertEqual([obj.flush_calls for obj in objects], [1, 1, 0, 0, 0])
        self.assertEqual(list(_FakeModel.__instance_cache__), [3, 4, 5, 1, 2])

    def test_replacement_between_captured_entries_is_not_touched_or_resurrected(self):
        replacement = _FakeObject(2, retain=True)
        second = _FakeObject(2, retain=True)

        def replace_second():
            _FakeModel.__instance_cache__[2] = replacement

        first = _FakeObject(1, retain=True, on_flush=replace_second)
        _FakeModel.__instance_cache__ = {1: first, 2: second}
        sweep = self._sweep(batch_size=2)

        sweep.run_turn()

        self.assertEqual(first.flush_calls, 1)
        self.assertEqual(second.flush_calls, 0)
        self.assertEqual(replacement.flush_calls, 0)
        self.assertIs(_FakeModel.__instance_cache__[2], replacement)

    def test_removal_between_turns_does_not_reprocess_retained_entries(self):
        objects = [_FakeObject(key, retain=True) for key in range(1, 4)]
        _FakeModel.__instance_cache__ = {obj.pk: obj for obj in objects}
        sweep = self._sweep(batch_size=1)

        self.assertTrue(sweep.run_turn())
        _FakeModel.__instance_cache__.pop(2)
        _FakeModel.__instance_cache__.pop(3)

        self.assertFalse(sweep.run_turn())
        self.assertEqual([obj.flush_calls for obj in objects], [1, 0, 0])

    def test_bulk_query_failure_retains_unknown_entries(self):
        obj = _FakeObject(1, retain=True, row_check=True)
        _FakeModel.__instance_cache__ = {1: obj}
        sweep = self._sweep()

        with (
            patch.object(
                idmapper,
                "_missing_row_entry_ids",
                side_effect=DatabaseError("database unavailable"),
            ),
            self.assertRaises(DatabaseError),
        ):
            sweep.run_turn()

        self.assertIs(_FakeModel.__instance_cache__[1], obj)
        self.assertEqual(obj.flush_calls, 0)
        self.assertEqual(obj.missing_calls, 0)

    def test_time_budget_yields_after_at_least_one_entry(self):
        objects = [_FakeObject(key, retain=True) for key in range(1, 4)]
        _FakeModel.__instance_cache__ = {obj.pk: obj for obj in objects}
        sweep = self._sweep(batch_size=3, budget_ms=1)

        with patch.object(idmapper.time, "monotonic", side_effect=[0.0, 0.002, 0.002]):
            self.assertTrue(sweep.run_turn())

        self.assertEqual([obj.flush_calls for obj in objects], [1, 0, 0])

    def test_cancelled_epoch_is_a_noop(self):
        obj = _FakeObject(1, retain=True)
        _FakeModel.__instance_cache__ = {1: obj}
        sweep = self._sweep()
        sweep.cancel()

        self.assertFalse(sweep.run_turn())
        self.assertEqual(obj.flush_calls, 0)

    def test_scheduled_failure_clears_active_epoch_and_retains_cache(self):
        from evennia.utils import defer

        obj = _FakeObject(1, retain=True, row_check=True)
        _FakeModel.__instance_cache__ = {1: obj}
        sweep = self._sweep()
        idmapper._ACTIVE_FLUSH_SWEEP = sweep
        self.addCleanup(setattr, idmapper, "_ACTIVE_FLUSH_SWEEP", None)
        future = concurrent.futures.Future()
        future.set_exception(DatabaseError("database unavailable"))

        with (
            patch.object(
                defer,
                "in_thread",
                return_value=future,
            ),
            patch.object(
                idmapper.clock,
                "call_later",
                side_effect=lambda _delay, callback, *args: callback(*args),
            ),
            patch.object(idmapper.logger, "log_trace"),
        ):
            sweep._run_scheduled_turn()

        self.assertIsNone(idmapper._ACTIVE_FLUSH_SWEEP)
        self.assertIs(_FakeModel.__instance_cache__[1], obj)

    def test_automatic_row_query_resumes_on_scheduled_io_callback(self):
        from evennia.utils import defer

        obj = _FakeObject(1, retain=True, row_check=True)
        _FakeModel.__instance_cache__ = {1: obj}
        sweep = self._sweep()
        idmapper._ACTIVE_FLUSH_SWEEP = sweep
        self.addCleanup(setattr, idmapper, "_ACTIVE_FLUSH_SWEEP", None)
        future = concurrent.futures.Future()
        scheduled = []

        def capture(_delay, callback, *args):
            scheduled.append((callback, args))
            return type("Handle", (), {"cancel": lambda self: None})()

        with (
            patch.object(defer, "in_thread", return_value=future) as in_thread,
            patch.object(idmapper.clock, "call_later", side_effect=capture),
        ):
            sweep._run_scheduled_turn()
            self.assertEqual(obj.flush_calls, 0)
            in_thread.assert_called_once()

            future.set_result((set(), 1))
            callback, args = scheduled.pop(0)
            callback(*args)

        self.assertEqual(obj.flush_calls, 1)
        self.assertEqual(sweep.stats["row_queries"], 1)
        sweep.cancel()

    def test_schedule_uses_engine_isolated_clock_callback(self):
        sweep = self._sweep()
        handle = object()

        with patch.object(idmapper.clock, "call_later", return_value=handle) as call_later:
            sweep.schedule()

        call_later.assert_called_once_with(0, sweep._run_scheduled_turn)
        self.assertIs(sweep.handle, handle)

    def test_backing_row_queries_respect_backend_parameter_limit(self):
        manager = _FakeQueryManager()
        objects = [_FakeObject(key, row_check=True) for key in range(1, 6)]
        entries = [({}, obj.pk, obj) for obj in objects]

        with (
            patch.object(_FakeModel, "_base_manager", manager, create=True),
            patch.object(
                type(idmapper.connections["default"].features),
                "max_query_params",
                new_callable=PropertyMock,
                return_value=2,
            ),
        ):
            missing, query_count = idmapper._missing_row_entry_ids(entries)

        self.assertEqual(missing, set())
        self.assertEqual(query_count, 3)
        self.assertEqual(manager.calls, [[1, 2], [3, 4], [5]])

    def test_epoch_markers_need_no_aggregate_finalization(self):
        objects = [_FakeObject(key, retain=True) for key in range(1, 6)]
        _FakeModel.__instance_cache__ = {obj.pk: obj for obj in objects}
        sweep = self._sweep(batch_size=2)

        while sweep.run_turn():
            pass

        self.assertFalse(hasattr(sweep, "processed_tokens"))
        self.assertTrue(
            all(obj.__dict__[idmapper._IDMAPPER_SWEEP_EPOCH_ATTR] is sweep.epoch for obj in objects)
        )

    def test_non_jsonb_model_creation_skips_tombstone_ownership_hook(self):
        class Meta:
            def get_field(self, name):
                raise FieldDoesNotExist(name)

        instance = type(
            "Instance",
            (),
            {"_meta": Meta(), "cache_instance": lambda self: None},
        )()
        sender = type(
            "Sender",
            (),
            {"cache_instance": staticmethod(lambda value: None)},
        )

        with (
            patch.object(idmapper, "_jsonb_attribute_backend_active", return_value=True),
            patch.object(sender, "cache_instance") as cache_instance,
        ):
            idmapper.update_cached_instance(sender, instance, created=True)

        cache_instance.assert_called_once_with(instance)
