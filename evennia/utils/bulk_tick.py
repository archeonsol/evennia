"""
Bulk-tick engine: event-loop-thread-safe coordinator for vectorised attribute ticks.

Three-phase pattern for every tick that mutates scalar attributes on many objects:

    Phase 1  [event loop thread]   BulkTickContext.gather_objectdb()
        Snapshots raw values straight from in-process L1 dicts — zero SQL,
        zero deserialization.  Must run on the event loop because the idmapper
        and L1 dicts are not concurrency-safe.

        Phase 1b: SQL bulk-read for uncached objects (blocking I/O on the event
        loop thread, acceptable at <10 000 objects; move to Phase 2 worker if
        event-loop time exceeds 5ms at scale).

    Phase 2  [worker thread]    caller-supplied computation (e.g. Polars)
        Pure CPU work on plain Python dicts.  No game-object access; safe to
        run off the event loop via evennia.utils.defer.in_thread.

    Phase 3  [event loop thread]   BulkTickContext.apply()
        Writes per-row results back to L1 dicts and marks backends dirty.
        The normal write-behind flush (maintenance tick) persists to Postgres.
        For uncached objects, writes directly under the same spool-first,
        row-second coordinator used by protected Attribute persistence.
        Must run on the reactor.

        Writes are compare-and-set: Phase 1 records each object's default-
        category values as a baseline, and Phase 3 only writes a key whose
        stored value still equals that baseline. A combat hit or player action
        that changed the value between gather and apply is detected and the
        stale heartbeat result is skipped (counted in ``ctx.conflicts``) rather
        than blindly overwriting newer state.

Why not bypass write-behind with direct SQL for cached objects?
    The maintenance tick flushes the *entire* db_attrs document from L1.  A
    partial jsonb_set(...) write directly to the DB would be overwritten by the
    next full-document flush from a stale L1.  Patching L1 directly keeps the
    two caches coherent and lets write-behind do its normal job.

    For *uncached* objects there is no same-process L1, but other processes and
    protected updates still exist. ``_apply_uncached`` therefore locks and
    compare-and-sets each row without instantiating partial idmapper models.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.db import connections, router, transaction
from django.db.models import F

from evennia.typeclasses.jsonb_handler import (
    AttributeUpdateError,
    AttributeUpdateUnavailable,
    AttributeUpdateUsageError,
    JsonbAttributeBackend,
    _existing_row_state,
    _has_user_transaction,
    _list_row_entries,
    _quarantine_cache_sync,
    _regular_backend_for_state,
    _require_io_thread,
    _spool_row_lock,
    _sync_live_backends,
    protected_mutation_active,
)
from evennia.typeclasses.jsonb_util import to_jsonb

_NULL_CAT = "~"  # db_attrs key for the default (category=None) section
_MISSING = object()  # sentinel: attribute key absent from a section


def _assert_io_thread(where: str) -> None:
    """Fail fast if not on the process event-loop thread (idmapper/L1 are not thread-safe)."""
    _require_io_thread(where)


class BulkTickContext:
    """
    Event-loop-side coordinator for a single bulk-tick cycle.

    Usage::

        # Phase 1 — on reactor
        ctx = BulkTickContext()
        ctx.gather_objectdb(ids, snapshot_fn)

        # Phase 2 — in worker (compute on ctx.rows, e.g. via Polars)
        result = compute_fn(ctx.rows)

        # Phase 3 — on event loop (callback from defer.in_thread)
        ctx.apply(result)

    The caller supplies a ``snapshot_fn(obj_id, l1_dict) -> dict`` that
    extracts whatever fields the computation needs from the L1 attribute dict.
    The returned dict must include an ``"id"`` key.
    """

    def __init__(self) -> None:
        # id → backend kept on the reactor side so backends never cross threads.
        self._backends: dict[int, JsonbAttributeBackend] = {}
        # IDs gathered via SQL (no L1 backend); written back via ORM in apply().
        self._uncached_ids: set[int] = set()
        # Plain Python dicts — safe to hand to a worker thread (no game objects).
        self.rows: list[dict[str, Any]] = []
        # id → {attr_key: stored value at gather time} — the compare-and-set
        # baseline. apply() only writes a key whose current stored value still
        # equals this, so a combat/player write landing between gather and
        # apply is detected and skipped rather than blindly overwritten.
        self._baseline: dict[int, dict[str, Any]] = {}
        # id → {attr_key: (global epoch, path epoch)} for cached-path ABA
        # detection. Uncached rows start at epoch zero if they become cached.
        self._epochs: dict[int, dict[str, tuple[int, int]]] = {}
        # Count of (object, key) writes skipped because the source changed
        # under us since gather. Readable by the caller after apply().
        self.conflicts: int = 0

    # ------------------------------------------------------------------
    # Phase 1
    # ------------------------------------------------------------------

    def gather_objectdb(
        self,
        obj_ids: list[int],
        snapshot_fn,
    ) -> "BulkTickContext":
        """
        Snapshot L1 attribute values for a list of ObjectDB PKs.

        ``snapshot_fn(obj_id: int, l1: dict) -> dict`` is called for each
        object that has a JsonbAttributeBackend.  It must return a plain dict
        (no game objects) that includes an ``"id"`` key.

        Cached objects: snapshot taken from the in-process L1 dict (zero SQL).
        Uncached objects: db_attrs read from Postgres via ORM (Phase 1b —
        blocking I/O on the reactor; acceptable at <10 000 objects).

        Must run on the event loop thread.
        Returns self for chaining.
        """
        _assert_io_thread("gather_objectdb")
        if protected_mutation_active():
            raise AttributeUpdateUsageError(
                "BulkTickContext.gather_objectdb may not run inside blocking_update."
            )

        from evennia.objects.models import ObjectDB

        alias = router.db_for_read(ObjectDB) or "default"
        if _has_user_transaction(connections[alias]):
            raise AttributeUpdateUsageError("BulkTickContext.gather_objectdb requires autocommit.")

        uncached_ids = []
        for obj_id in obj_ids:
            key = (alias, ObjectDB._meta.label_lower, obj_id)
            state = _existing_row_state(key)
            backend = _regular_backend_for_state(state)
            if state is not None and backend is None:
                raise AttributeUpdateUnavailable(
                    f"ObjectDB#{obj_id} has live JSONB state without a usable handler."
                )
            if state is None:
                obj = ObjectDB.get_cached_instance(obj_id)
                if obj is None:
                    uncached_ids.append(obj_id)
                    continue
                try:
                    if "db_attrs" not in obj.__dict__:
                        # Cached instances may have db_attrs deferred; touching the
                        # descriptor triggers refresh_from_db and can KeyError during
                        # partial loads. Seed __dict__ directly from SQL instead.
                        row = (
                            ObjectDB.objects.filter(pk=obj_id)
                            .values_list("db_attrs", flat=True)
                            .first()
                        )
                        obj.__dict__["db_attrs"] = row if isinstance(row, dict) else {}
                    backend = obj.attributes.backend
                except AttributeError:
                    continue
            if not isinstance(backend, JsonbAttributeBackend):
                continue

            backend._assert_readable()
            self._backends[obj_id] = backend
            # CAS baseline: a shallow copy of the default-category data dict as
            # it stands now. Values here are scalars replaced by reference on
            # write, so a concurrent mutation shows up as an inequality.
            self._baseline[obj_id] = dict(backend._l1.get(_NULL_CAT, {}).get("_d", {}))
            snapshot = snapshot_fn(obj_id, backend._l1)
            self._epochs[obj_id] = {
                key: backend._path_generation(key, None) for key in snapshot if key != "id"
            }
            self.rows.append(snapshot)

        # Phase 1b — SQL read for objects not in idmapper
        if uncached_ids:
            self._gather_uncached(uncached_ids, snapshot_fn)

        return self

    def _gather_uncached(self, uncached_ids: list[int], snapshot_fn) -> None:
        """Read db_attrs from Postgres for objects not in the idmapper cache."""
        from evennia.objects.models import ObjectDB

        for obj_id, attrs in ObjectDB.objects.filter(id__in=uncached_ids).values_list(
            "id", "db_attrs"
        ):
            attrs = attrs if isinstance(attrs, dict) else {}
            self._uncached_ids.add(obj_id)
            self._baseline[obj_id] = dict(attrs.get(_NULL_CAT, {}).get("_d", {}))
            snapshot = snapshot_fn(obj_id, attrs)
            self._epochs[obj_id] = {key: (0, 0) for key in snapshot if key != "id"}
            self.rows.append(snapshot)

    # ------------------------------------------------------------------
    # Phase 3
    # ------------------------------------------------------------------

    def apply(self, updates: list[dict[str, Any]]) -> int:
        """
        Write computed values back to L1 dicts and mark backends dirty.

        Each item in *updates* must have an ``"id"`` key (int) matching an
        object gathered in Phase 1, plus one or more ``{attr_key: new_value}``
        pairs for the *default category* (``"~"``).

        Cached objects: patched in-process; write-behind persists to Postgres.
        Uncached objects: written directly to db_attrs via ORM.

        Must run on the event loop thread.
        Returns the number of objects whose L1 / DB was actually mutated.
        """
        _assert_io_thread("apply")
        if protected_mutation_active():
            raise AttributeUpdateUsageError(
                "BulkTickContext.apply may not run inside blocking_update."
            )
        from evennia.objects.models import ObjectDB

        alias = router.db_for_write(ObjectDB) or "default"
        if _has_user_transaction(connections[alias]):
            raise AttributeUpdateUsageError("BulkTickContext.apply requires autocommit.")

        patched = 0
        sql_batch: dict[int, dict[str, Any]] = {}

        for row in updates:
            obj_id = row.get("id")
            backend = self._backends.get(obj_id)

            # An object uncached at gather time may have been loaded into the
            # idmapper between Phase 1 and Phase 3.  Reroute to the L1 path
            # so the in-process cache stays coherent with what we write.
            if backend is None and obj_id in self._uncached_ids:
                alias = router.db_for_write(ObjectDB) or "default"
                state = _existing_row_state((alias, ObjectDB._meta.label_lower, obj_id))
                if state is not None:
                    backend = _regular_backend_for_state(state)
                    if backend is None:
                        self.conflicts += 1
                        continue
                else:
                    obj = ObjectDB.get_cached_instance(obj_id)
                    if obj is None:
                        obj = None
                    try:
                        if obj is not None:
                            candidate = obj.attributes.backend
                            if isinstance(candidate, JsonbAttributeBackend):
                                backend = candidate
                    except AttributeError:
                        pass

            if backend is not None:
                # cached path: write to L1 under compare-and-set
                backend._assert_mutation_allowed()
                section = backend._l1.get(_NULL_CAT, {}).get("_d", {})
                baseline = self._baseline.get(obj_id, {})
                epochs = self._epochs.get(obj_id, {})
                applicable = {}
                for key, val in row.items():
                    if key == "id":
                        continue
                    # CAS: only write if the stored value is still what we
                    # gathered. A mismatch means combat/player action changed
                    # it since gather — skip so the newer state is not erased.
                    if section.get(key, _MISSING) != baseline.get(
                        key, _MISSING
                    ) or backend._path_generation(key, None) != epochs.get(key, (0, 0)):
                        self.conflicts += 1
                        continue
                    applicable[key] = to_jsonb(val)

                if applicable:
                    backend._l1.setdefault(_NULL_CAT, {}).setdefault("_d", {}).update(applicable)
                    backend._row_state.mark_dirty()
                    for key in applicable:
                        backend._bump_path_generation(key, None)
                    try:
                        _sync_live_backends(backend._row_state, invalidate=False)
                    except Exception as err:
                        _quarantine_cache_sync(backend._row_state)
                        raise AttributeUpdateUnavailable(
                            "BulkTick could not reconcile live JSONB handlers."
                        ) from err
                    patched += 1
            elif obj_id in self._uncached_ids:
                # uncached path: accumulate for SQL write-back
                sql_batch[obj_id] = {k: v for k, v in row.items() if k != "id"}

        if sql_batch:
            patched += self._apply_uncached(sql_batch)

        return patched

    def _apply_uncached(self, batch: dict[int, dict[str, Any]]) -> int:
        """
        Write back to db_attrs for objects not in the idmapper cache.

        Each row acquires the durable spool lock before its database row lock,
        checks the gather baseline, and updates once. This deliberately gives
        up the former CASE/WHEN bulk write so uncached ticks cannot overwrite a
        protected update between their read and write. Partial ObjectDB model
        instances are never constructed.
        """
        from evennia.objects.models import ObjectDB

        alias = router.db_for_write(ObjectDB) or "default"
        connection = connections[alias]
        manager = ObjectDB._base_manager.using(alias)
        written = 0
        for obj_id in sorted(batch):
            updates = batch[obj_id]
            key = (alias, ObjectDB._meta.label_lower, int(obj_id))
            try:
                with _spool_row_lock(key):
                    if _list_row_entries(key):
                        self.conflicts += len(updates)
                        continue
                    with transaction.atomic(using=alias):
                        if connection.vendor == "sqlite":
                            manager.filter(pk=obj_id).update(db_attrs=F("db_attrs"))
                        row = (
                            manager.select_for_update().filter(pk=obj_id).values("db_attrs").first()
                        )
                        if row is None:
                            continue
                        document = deepcopy(row["db_attrs"] or {})
                        current = document.get(_NULL_CAT, {}).get("_d", {})
                        baseline = self._baseline.get(obj_id, {})
                        applicable = {}
                        for attr_key, value in updates.items():
                            if current.get(attr_key, _MISSING) != baseline.get(attr_key, _MISSING):
                                self.conflicts += 1
                                continue
                            applicable[attr_key] = to_jsonb(value)
                        if not applicable:
                            continue
                        document.setdefault(_NULL_CAT, {}).setdefault("_d", {}).update(applicable)
                        if manager.filter(pk=obj_id).update(db_attrs=document) != 1:
                            continue
                    written += 1
            except AttributeUpdateError:
                self.conflicts += len(updates)
        return written
