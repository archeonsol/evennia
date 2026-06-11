"""
Bulk-tick engine: reactor-safe coordinator for vectorised attribute ticks.

Three-phase pattern for every tick that mutates scalar attributes on many objects:

    Phase 1  [reactor thread]   BulkTickContext.gather_objectdb()
        Snapshots raw values straight from in-process L1 dicts — zero SQL,
        zero deserialization.  Must run on the reactor because the idmapper
        and L1 dicts are not concurrency-safe.

        Phase 1b: SQL bulk-read for uncached objects (blocking I/O on reactor,
        acceptable at <10 000 objects; move to Phase 2 worker if reactor
        time exceeds 5ms at scale).

    Phase 2  [worker thread]    caller-supplied computation (e.g. Polars)
        Pure CPU work on plain Python dicts.  No game-object access; safe to
        run off the reactor via evennia.utils.defer.in_thread.

    Phase 3  [reactor thread]   BulkTickContext.apply()
        Writes per-row results back to L1 dicts and marks backends dirty.
        The normal write-behind flush (maintenance tick) persists to Postgres.
        For uncached objects, writes directly to db_attrs via ORM (safe because
        there is no in-process L1 that could overwrite the DB write).
        Must run on the reactor.

Why not bypass write-behind with direct SQL for cached objects?
    The maintenance tick flushes the *entire* db_attrs document from L1.  A
    partial jsonb_set(...) write directly to the DB would be overwritten by the
    next full-document flush from a stale L1.  Patching L1 directly keeps the
    two caches coherent and lets write-behind do its normal job.

    For *uncached* objects there is no conflicting L1, so direct DB writes are
    safe.  _apply_uncached reads all uncached rows in one query and writes back
    with a single CASE/WHEN UPDATE — one round-trip regardless of batch size.
    All uncached access goes through values_list/update, never model instances:
    partial instantiation of idmapper models is unsupported.
"""

from __future__ import annotations

from typing import Any

from evennia.typeclasses.jsonb_handler import JsonbAttributeBackend

_NULL_CAT = "~"  # db_attrs key for the default (category=None) section


def _assert_reactor_thread(where: str) -> None:
    """Fail fast if not on the Twisted I/O thread (idmapper/L1 are not thread-safe).

    Uses ``twisted.python.threadable.isInIOThread`` — the portable API.  Some
    Twisted builds expose ``reactor.isInIOThread`` only on newer releases; prod
    EPollReactor lacks that method and raised AttributeError every heartbeat tick.
    """
    from twisted.python import threadable

    assert threadable.isInIOThread(), f"{where} must run on the reactor thread"


class BulkTickContext:
    """
    Reactor-side coordinator for a single bulk-tick cycle.

    Usage::

        # Phase 1 — on reactor
        ctx = BulkTickContext()
        ctx.gather_objectdb(ids, snapshot_fn)

        # Phase 2 — in worker (compute on ctx.rows, e.g. via Polars)
        result = compute_fn(ctx.rows)

        # Phase 3 — on reactor (callback from defer.in_thread)
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

        Must run on the reactor thread.
        Returns self for chaining.
        """
        _assert_reactor_thread("gather_objectdb")

        from evennia.objects.models import ObjectDB

        uncached_ids = []
        for obj_id in obj_ids:
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

            self._backends[obj_id] = backend
            self.rows.append(snapshot_fn(obj_id, backend._l1))

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
            self.rows.append(snapshot_fn(obj_id, attrs))

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

        Must run on the reactor thread.
        Returns the number of objects whose L1 / DB was actually mutated.
        """
        _assert_reactor_thread("apply")

        patched = 0
        sql_batch: dict[int, dict[str, Any]] = {}

        for row in updates:
            obj_id = row.get("id")
            backend = self._backends.get(obj_id)

            # An object uncached at gather time may have been loaded into the
            # idmapper between Phase 1 and Phase 3.  Reroute to the L1 path
            # so the in-process cache stays coherent with what we write.
            if backend is None and obj_id in self._uncached_ids:
                from evennia.objects.models import ObjectDB

                obj = ObjectDB.get_cached_instance(obj_id)
                if obj is not None:
                    try:
                        candidate = obj.attributes.backend
                        if isinstance(candidate, JsonbAttributeBackend):
                            backend = candidate
                    except AttributeError:
                        pass

            if backend is not None:
                # cached path: write to L1
                section = backend._l1.setdefault(_NULL_CAT, {}).setdefault("_d", {})
                dirty = False
                for key, val in row.items():
                    if key == "id":
                        continue
                    section[key] = val
                    dirty = True

                if dirty:
                    backend._mark_dirty()
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

        Safe because there is no in-process L1 to conflict with these writes.
        Reads all rows in one values_list query, merges in Python, then writes
        back with a single CASE/WHEN UPDATE (the same SQL bulk_update emits) —
        one round-trip regardless of batch size. Never instantiates ObjectDB
        from partial rows: partial instantiation of idmapper models is
        unsupported (construction reads deferred fields, and idmapper cannot
        refresh a deferred field).
        """
        from django.db.models import Case, Value, When

        from evennia.objects.models import ObjectDB

        docs: dict[int, dict] = {}
        for obj_id, attrs in ObjectDB.objects.filter(id__in=batch.keys()).values_list(
            "id", "db_attrs"
        ):
            updates = batch.get(obj_id)
            if not updates:
                continue
            doc = dict(attrs) if isinstance(attrs, dict) else {}
            doc.setdefault(_NULL_CAT, {}).setdefault("_d", {}).update(updates)
            docs[obj_id] = doc

        if docs:
            field = ObjectDB._meta.get_field("db_attrs")
            ObjectDB.objects.filter(pk__in=docs).update(
                db_attrs=Case(
                    *(
                        When(pk=obj_id, then=Value(doc, output_field=field))
                        for obj_id, doc in docs.items()
                    )
                )
            )

        return len(docs)
