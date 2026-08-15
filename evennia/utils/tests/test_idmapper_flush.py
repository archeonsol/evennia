"""Tests for bounded idmapper pressure sweeps."""

from unittest.mock import patch

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
        obj = _FakeObject(1, retain=True, row_check=True)
        _FakeModel.__instance_cache__ = {1: obj}
        sweep = self._sweep()
        idmapper._ACTIVE_FLUSH_SWEEP = sweep
        self.addCleanup(setattr, idmapper, "_ACTIVE_FLUSH_SWEEP", None)

        with (
            patch.object(
                idmapper,
                "_missing_row_entry_ids",
                side_effect=DatabaseError("database unavailable"),
            ),
            patch.object(idmapper.logger, "log_trace"),
        ):
            sweep._run_scheduled_turn()

        self.assertIsNone(idmapper._ACTIVE_FLUSH_SWEEP)
        self.assertIs(_FakeModel.__instance_cache__[1], obj)

    def test_schedule_uses_engine_isolated_clock_callback(self):
        sweep = self._sweep()
        handle = object()

        with patch.object(idmapper.clock, "call_later", return_value=handle) as call_later:
            sweep.schedule()

        call_later.assert_called_once_with(0, sweep._run_scheduled_turn)
        self.assertIs(sweep.handle, handle)
