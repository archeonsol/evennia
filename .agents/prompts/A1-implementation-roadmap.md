# A1 Implementation Roadmap: JSONB Attribute Storage

**Status:** approved direction, not yet started  
**Scope:** engine fork (archeonsol/evennia) + downstream (mootest), lockstep  
**Prerequisite reading:** `A1-attribute-descriptors.md`, `engine-api-architecture.md`, `engine-long-horizon.md`

---

## Problem Summary

Every attribute on every object is a separate row in `AttributeDB`. Loading one
object with 15 attributes costs 15 rows of I/O or 15 Redis round-trips. Combat
ticks iterating 200 characters with 3 hot attrs each = 600 cache ops per tick.
Filtered queries on attribute values are impossible in SQL. Schema changes (key
renames, type changes) leave stale persisted data silently.

The current stack already addresses write amplification (write-behind +
`bulk_update`) and primitive queryability (`db_int_val` / `db_float_val` /
`db_str_val` typed columns). What it does not solve:

- **Fan-out row count**: N attrs per object = N rows. One row per object solves
  this permanently.
- **Arbitrary queryability**: typed columns cover primitives only. JSONB with a
  GIN index covers every key/value combination.
- **Schema story**: no versioning, no migration, silent stale key rot.
- **API proliferation**: `.db`, `.attributes.add/get`, `AttributeProperty`,
  batched-blob handlers — no canonical guidance, no declared cost.

---

## Target Architecture

```
┌─────────────────────────────────────────────┐
│  Python process (Twisted reactor)           │
│                                             │
│  L1: in-process dict (idmapper)             │  ← mutations dirty-track object
│      obj._attrs_cache = {"hp": 80, ...}     │    no individual attr objects
│      obj._session_cache = {...}             │    ndb equivalent, same shape
└──────────────┬──────────────────────────────┘
               │ flush tick (one UPDATE per dirty object)
┌──────────────▼──────────────────────────────┐
│  PostgreSQL                                 │
│                                             │
│  TypedObject base:                          │
│    db_attrs   JSONB DEFAULT '{}'            │  ← all persistent attrs
│    db_session JSONB DEFAULT '{}'  UNLOGGED* │  ← transient attrs (ndb survives
│                                             │    process restart, not crash)
│  Hot queryable scalars → real indexed cols  │  ← promoted per TypedAttr decl
│  GIN index on db_attrs                      │  ← arbitrary key queries
└─────────────────────────────────────────────┘
               │ bulk tick reads
┌──────────────▼──────────────────────────────┐
│  Polars DataFrames (per-tick, in-memory)    │  ← vectorized game logic
└─────────────────────────────────────────────┘
               │ narrow use only
┌──────────────▼──────────────────────────────┐
│  Redis                                      │  ← leaderboards, pub/sub,
│  (not attr cache after this migration)      │    presence, rate limiting
└─────────────────────────────────────────────┘
```

*`UNLOGGED` is a PostgreSQL table-level option. Since `db_session` is added as a
column to the existing `TypedObject`-derived tables, not a separate table, we
achieve equivalent semantics by keeping `db_session` out of the flush path and
clearing it explicitly on server start rather than relying on PostgreSQL UNLOGGED.
See Phase 3.*

---

## JSONB Document Schema

```json
{
  "_v": 1,
  "_": {
    "hp": 80,
    "faction": "corp",
    "equipment": "<msgpack-base64-blob>"
  },
  "nick": {
    "te": "the",
    "bb": "bulletin board"
  },
  "combat": {
    "stance": "aggressive"
  }
}
```

- Top-level `"_"` key: attributes with `category=None` (the vast majority).
- Top-level named keys: attribute categories (nick, combat, etc.).
- `"_v"`: schema version integer, used by the migration runner (Phase 6).
- Complex Python objects (non-JSON-serializable): msgpack-encoded, stored as
  base64 string under a sentinel prefix `"\x00mp:"` so the handler can
  distinguish them from plain strings at read time.
- GIN index on `db_attrs` covers `db_attrs @> '{"_": {"faction": "corp"}}'`
  and `db_attrs -> '_' ->> 'faction' = 'corp'` style queries.

---

## Phases

Phases are **sequenced by dependency**. Each phase must leave the test suite
green before the next starts. Downstream (mootest) impact is noted per phase.

---

### Phase 0 — Dependencies and Scaffolding

**Goal:** Add new Python dependencies; wire detection guard; no behavior change.

**Engine changes:**

1. Add to `setup.cfg` / `pyproject.toml` optional extras:
   ```
   polars >= 1.0
   msgpack >= 1.0
   ```
   Neither is imported unconditionally. Guard all import sites:
   ```python
   try:
       import polars as pl
       _POLARS_AVAILABLE = True
   except ImportError:
       _POLARS_AVAILABLE = False
   ```

2. Add `evennia/typeclasses/jsonb_util.py` — serialization primitives:
   - `to_jsonb(value) -> JSON-safe` — routes primitives/dicts/lists directly;
     msgpack-encodes everything else, base64-wraps, prefixes `"\x00mp:"`.
   - `from_jsonb(raw) -> Python value` — inverse; detects sentinel prefix.
   - `classify_for_jsonb(value) -> ("json" | "msgpack", serialized)` — used by
     the handler to decide storage path.
   - Unit tests: round-trip for every type currently handled by `_classify_value`
     plus class instances, tuples, nested structures.

3. Add `evennia/typeclasses/jsonb_query.py` — query helpers:
   - `jsonb_filter(key, value, category=None, prefix="") -> dict` — returns
     Django ORM filter kwargs targeting `db_attrs` JSONB path. Used identically
     to the existing `value_query_filter`.

**Downstream:** Add `polars` and `msgpack` to `requirements.txt`. No settings
change yet.

**Done when:** `jsonb_util` round-trip tests pass; `jsonb_query` filter dict
tests pass; no existing tests broken.

---

### Phase 1 — Schema: Add JSONB Columns

**Goal:** Add `db_attrs` and `db_session` columns to all TypedObject-derived
models. Populate `db_attrs` from existing `AttributeDB` rows. Both storage
paths exist simultaneously; the old path remains active.

**Engine changes:**

1. In `evennia/typeclasses/models.py`, add to `TypedObject`:
   ```python
   from django.db.models import JSONField

   db_attrs = JSONField("attrs", default=dict, blank=True)
   db_session = JSONField("session", default=dict, blank=True)
   ```

2. Generate migration `0023_add_jsonb_columns.py`:
   ```python
   operations = [
       migrations.AddField(
           model_name="typedobject",  # applies to all concrete models
           name="db_attrs",
           field=models.JSONField(default=dict, blank=True),
       ),
       migrations.AddField(
           model_name="typedobject",
           name="db_session",
           field=models.JSONField(default=dict, blank=True),
       ),
   ]
   ```
   *Because `TypedObject` is abstract, the migration must add the column to each
   concrete model: `ObjectDB`, `AccountDB`, `ScriptDB`, `ChannelDB`. Verify the
   auto-generated migration covers all four.*

3. Add GIN index migration `0024_gin_index_db_attrs.py`:
   ```python
   from django.db import migrations
   from django.contrib.postgres.operations import GinIndex

   operations = [
       migrations.RunSQL(
           "CREATE INDEX CONCURRENTLY IF NOT EXISTS objects_db_attrs_gin "
           "ON objects_objectdb USING GIN (db_attrs);",
           reverse_sql="DROP INDEX IF EXISTS objects_db_attrs_gin;",
       ),
       # Repeat for accountdb, scriptdb, channeldb
   ]
   ```
   Use `CONCURRENTLY` — no table lock, safe on live production.

4. Add `evennia/typeclasses/management/commands/populate_jsonb.py` — management
   command to backfill `db_attrs` from `AttributeDB` rows:
   ```
   evennia populate_jsonb [--batch-size 500] [--model objects|accounts|scripts|channels|all]
   ```
   Algorithm per object:
   - Fetch all `Attribute` rows via `obj.db_attributes.all()`.
   - Build the JSONB document (category-keyed structure described above).
   - Decode existing pickle values via `from_pickle` then `to_jsonb`.
   - `bulk_update` in batches.
   - Log progress; resumable (skip objects where `db_attrs != {}`).

5. Add `evennia/typeclasses/tests/test_jsonb_migration.py`:
   - Creates objects with varied attribute types (int, float, str, bool, None,
     dict, list, tuple, class instance, nested).
   - Runs the population logic.
   - Asserts round-trip fidelity via `from_jsonb(to_jsonb(val)) == val` for
     every attribute.

**Downstream actions:**
- Run `evennia migrate`.
- Run `evennia populate_jsonb --model all` — one-time backfill.
- Verify with: `python manage.py shell -c "from evennia.objects.models import ObjectDB; o = ObjectDB.objects.first(); print(o.db_attrs)"`

**Done when:** All objects have populated `db_attrs`; round-trip test passes;
all existing tests pass; production `db_attrs` non-empty.

---

### Phase 2 — JSONB AttributeHandler (Parallel Path)

**Goal:** Build the new `JsonbAttributeHandler` that reads/writes against
`db_attrs` instead of the `AttributeDB` table. Wire it behind a settings flag.
Both handlers coexist; the flag switches which one is active.

**Engine changes:**

1. Add `evennia/typeclasses/jsonb_handler.py` — `JsonbAttributeHandler`:

   Public API identical to existing `AttributeHandler`:
   - `.get(key, category=None, default=None, ...)` 
   - `.add(key, value, category=None, ...)`
   - `.remove(key, category=None, ...)`
   - `.clear(category=None)`
   - `.all(category=None)`
   - `.has(key, category=None)`
   - `.__iter__`

   Internal design:
   - On first access: load `obj.db_attrs` dict into `obj._attrs_l1` (in-process
     cache, plain Python dict, keyed `(key, category)`).
   - Reads: `obj._attrs_l1[(key, category)]` — zero DB, zero Redis.
   - Writes: update `obj._attrs_l1`; add `obj` to module-level `_DIRTY_OBJECTS`
     WeakSet.
   - Flush tick: `bulk_update(list(_DIRTY_OBJECTS), ["db_attrs"])` — one DB
     write per dirty object regardless of how many attrs changed. Simpler than
     the existing per-attr dirty tracking.
   - No Redis cache. No write-behind complexity. One dict, one write.

   Lock storage: stored in `db_attrs` under reserved key `"_locks"`. Same
   serialization path.

   `strvalue` (nick system): stored normally; category `"nick"` in the document
   is the canonical nick namespace.

2. `NJsonbAttributeHandler` for `.ndb`:
   - Same API, operates on `obj._session_l1` backed by `obj.db_session`.
   - **NOT flushed by the tick handler by default.** Mark a separate
     `_DIRTY_SESSION_OBJECTS` set; flush only when `JSONB_PERSIST_SESSION=True`
     (default False). When False, `db_session` is never written — equivalent to
     current `.ndb` semantics. When True, survives process restart.
   - On server start: call `clear_all_session()` management command to zero
     `db_session` for all objects (mimics ndb reset-on-reload).

3. Settings flag in `evennia/settings_default.py`:
   ```python
   # "model" = current AttributeDB-backed handler (default during migration)
   # "jsonb"  = new JSONB handler
   ATTRIBUTE_STORAGE_BACKEND = "model"
   ```

4. In `TypedObject.__init__` or `at_init`, choose handler based on setting:
   ```python
   if settings.ATTRIBUTE_STORAGE_BACKEND == "jsonb":
       self.attributes = JsonbAttributeHandler(self)
       self.nattributes = NJsonbAttributeHandler(self)
   else:
       self.attributes = AttributeHandler(self, ...)  # existing
   ```
   `.db` and `.ndb` property accessors are already delegating to
   `self.attributes` and `self.nattributes` — no change needed there.

5. Tests in `evennia/typeclasses/tests/test_jsonb_handler.py`:
   - Port every test from `test_attribute_fork.py` to run against both handlers.
   - Parameterize: `@pytest.mark.parametrize("backend", ["model", "jsonb"])`.
   - Test: category isolation, locking, batch flush, concurrent dirty tracking,
     `AttributeProperty` delegation, NAttribute independence.

6. Flush integration:
   - Add `flush_jsonb_dirty()` alongside `flush_all_dirty()`.
   - Wire into `evennia/server/service.py` maintenance tick:
     ```python
     if settings.ATTRIBUTE_STORAGE_BACKEND == "jsonb":
         from evennia.typeclasses.jsonb_handler import flush_jsonb_dirty
         flush_jsonb_dirty()
     else:
         flush_all_dirty()
     ```

**Downstream actions:**
- Set `ATTRIBUTE_STORAGE_BACKEND = "jsonb"` in `settings.py` (staging first).
- Run full test suite against both values; diff must be clean.
- Soak in staging for at least one week before production flip.

**Done when:** All existing attribute tests pass with `backend="jsonb"`;
flush integrates with service tick; staging soak clean.

---

### Phase 3 — Redis L2 Removal

**Goal:** Remove the Redis attribute cache. It is no longer needed — JSONB cold
load is one query, in-process dict is L1. Redis stays for leaderboards, pub/sub,
presence.

**Engine changes:**

1. `evennia/typeclasses/redis_attr_cache.py` — deprecate:
   - Remove `RedisCachedModelAttributeBackend`.
   - Keep module stub with import guard that raises a clear error if
     `ATTRIBUTE_BACKEND_CLASS` still points here:
     ```python
     raise ImproperlyConfigured(
         "RedisCachedModelAttributeBackend was removed. "
         "Set ATTRIBUTE_STORAGE_BACKEND = 'jsonb'."
     )
     ```

2. `evennia/typeclasses/attributes.py` — remove:
   - `_DIRTY_BACKENDS`, `_ORPHAN_DIRTY_ATTRS`, `_DIRTY_ATTR_UPDATE_FIELDS`
   - `_mark_attr_dirty`, `_flush_orphan_dirty`, `flush_if_pending`,
     `flush_all_dirty`, `count_pending_dirty`, `_FLUSHING` thread-local
   - `ModelAttributeBackend.flush_dirty` (and entire `ModelAttributeBackend`
     class can be removed once Phase 4 drops `AttributeDB`)
   - `IAttributeBackend` / `InMemoryAttributeBackend` (no longer needed
     post-JSONB; the handler IS the backend)
   - `AttributeManager.get_queryset` `flush_if_pending` call

3. `evennia/server/service.py` — remove `flush_all_dirty` import and call;
   only `flush_jsonb_dirty` remains.

4. `evennia/server/prometheus_metrics.py` — remove attr-flush metrics:
   - `observe_attribute_dirty_pending`
   - `record_attribute_flush_stats`
   - Add new metric: `jsonb_flush_objects_total` (objects written per tick).

5. `evennia/typeclasses/attribute_metrics.py` — update or remove.

**Downstream actions:**
- Remove from `settings.py`:
  ```python
  ATTRIBUTE_BACKEND_CLASS = "..."
  ATTRIBUTE_REDIS_CACHE_ENABLED = True
  ATTRIBUTE_REDIS_CACHE_TTL = 120
  ```
- Remove `"attributes"` Redis cache alias from `CACHES` dict (keep `default`,
  `throttle`, `combat`).
- `ENGINE_HOT_ATTRIBUTE_KEYS` setting becomes a no-op (typed columns on the old
  model no longer exist) — remove it or document it as deprecated.

**Done when:** `redis_attr_cache.py` removed from active code path; service.py
references clean; test suite passes without Redis attr cache.

---

### Phase 4 — AttributeDB Removal

**Goal:** Delete the `Attribute` model, `db_attributes` M2M, all ORM-backed
attribute code. The JSONB handler is now the only path.

**Dependencies:** Phase 2 must be live in production; `db_attrs` fully populated
and confirmed as source of truth.

**Engine changes:**

1. Confirm via query that no `AttributeDB` row has data not in `db_attrs`:
   ```python
   # Run before dropping
   mismatches = check_attr_parity()  # compare AttributeDB rows vs db_attrs
   assert mismatches == 0
   ```
   Add this as a management command: `evennia check_attr_parity`.

2. Migration `0025_drop_attribute_tables.py`:
   ```python
   operations = [
       migrations.RemoveField("ObjectDB", "db_attributes"),
       migrations.RemoveField("AccountDB", "db_attributes"),
       migrations.RemoveField("ScriptDB", "db_attributes"),
       migrations.RemoveField("ChannelDB", "db_attributes"),
       migrations.DeleteModel("Attribute"),
   ]
   ```
   This drops:
   - `typeclasses_attribute` table (~millions of rows → freed storage)
   - All four M2M join tables (`objects_objectdb_attributes`, etc.)

3. `evennia/typeclasses/attributes.py` — remove:
   - `Attribute` model class
   - `AttributeManager`
   - `IAttribute` interface
   - `ModelAttributeBackend` (if not already removed in Phase 3)
   - `value_query_filter`, `_classify_value`, `_apply_classified_value`
   - `_is_json_safe`, `_JSON_PRIMITIVE_TYPES`, `_BIGINT_*` constants
   - Typed column constants (`_DIRTY_ATTR_UPDATE_FIELDS`, etc.)
   - All SharedMemoryModel imports if no longer needed

   What remains in `attributes.py`:
   - `AttributeProperty` (kept, now delegates to `JsonbAttributeHandler`)
   - `NAttributeProperty`
   - `NickHandler` (refactored to use JSONB category `"nick"`)
   - `NAttributeHandler` (now an alias for `NJsonbAttributeHandler`)

4. `evennia/typeclasses/models.py` — remove:
   - `db_attributes` field from `TypedObject`
   - `Attribute` import
   - `ModelAttributeBackend` wiring in `__init__`

5. `AttributeProperty` adaptation:
   - Currently delegates to `AttributeHandler.get/add`. Update delegation to
     `JsonbAttributeHandler.get/add`. API surface unchanged; game code unaffected.

6. Tags: `db_tags` M2M to `Tag` model is **separate** and intentionally left
   alone. Tags have different access patterns (bulk membership, category
   filtering) that the current row model handles well. Tags are out of scope
   for this migration.

**Downstream actions:**
- Run `evennia migrate` in production during a maintenance window.
- `db_attributes` queryset references in game code — audit with:
  ```
  grep -rn "db_attributes\|AttributeDB\|from_pickle\|to_pickle" world/
  ```
  Any hit is a bug. The grep should be clean before running the migration.

**Done when:** `typeclasses_attribute` table dropped; `AttributeDB` model gone;
full test suite passes; no `db_attributes` references in game code.

---

### Phase 5 — TypedAttr Descriptor System

**Goal:** Declare storage shape and access pattern at the typeclass definition
site. Storage cost visible at a glance. Two backends: `"jsonb"` (default,
everything goes here) and `"real_col"` (promoted to an actual indexed SQL
column).

**Engine changes:**

1. `evennia/typeclasses/typed_attr.py` — new file:

   ```python
   class AttrField:
       """Schema entry for a TypedAttr bag sub-key."""
       def __init__(self, type_=None, default=None, min=None, max=None,
                    choices=None, nullable=True):
           ...

   class TypedAttr:
       """
       Descriptor for declared attribute state on a TypedObject.

       backend="jsonb"     — value in db_attrs JSONB document (default)
       backend="real_col"  — value in a real indexed column on the model
                             (requires a migration; enables btree queries)
       backend="bag"       — sub-dict in db_attrs, schema-declared keys,
                             lazy-loaded, one JSONB path

       All three expose identical get/set syntax to game code.
       """
       def __init__(self, type_=None, default=_UNSET, backend="jsonb",
                    category=None, keys=None, version=None,
                    db_index=False, null=True):
           ...

       def __set_name__(self, owner, name):
           self.attr_key = name
           # Register with owner's _typed_attrs registry (used by schema runner)
           owner._typed_attrs = getattr(owner, "_typed_attrs", {})
           owner._typed_attrs[name] = self

       def __get__(self, obj, objtype=None):
           if obj is None:
               return self
           if self.backend == "real_col":
               return getattr(obj, f"_rc_{self.attr_key}", self.default)
           if self.backend == "bag":
               return obj._attrs_l1.get_bag(self.attr_key)
           return obj.attributes.get(self.attr_key, default=self.default,
                                      category=self.category)

       def __set__(self, obj, value):
           if self.type_ is not None and not isinstance(value, self.type_):
               raise TypeError(f"{self.attr_key} expected {self.type_.__name__}")
           if self.backend == "real_col":
               setattr(obj, f"_rc_{self.attr_key}", value)
               _DIRTY_REAL_COL_OBJECTS.add(obj)
               return
           if self.backend == "bag":
               obj._attrs_l1.set_bag(self.attr_key, value)
               return
           obj.attributes.add(self.attr_key, value, category=self.category)
   ```

   `backend="bag"` semantics:
   - Stored as a sub-dict under key `attr_key` in `db_attrs["_"]`.
   - `char.stats.strength += 1` — reads the bag dict, updates in-place, marks
     the whole object dirty (same flush path as any other attr write).
   - Schema-declared `keys={}` enables defaults and validation per sub-key.
   - No extra DB row. One JSONB path.

   `backend="real_col"` semantics:
   - Does NOT interact with `db_attrs` at all.
   - Gets/sets a real Django column on the model.
   - Requires the developer to write a migration adding the column.
   - Flush: separate `bulk_update` for `_DIRTY_REAL_COL_OBJECTS`, called from
     the same tick handler as `flush_jsonb_dirty`.
   - Query API: `Character.objects.filter(level__gt=10)` — standard Django ORM.
   - Engine provides no auto-migration; developer runs `makemigrations`.

2. `AttributeBag` class (lives in `typed_attr.py`):
   - Thin dict wrapper with attribute-style access.
   - Holds a reference to the parent object for dirty-tracking.
   - `__getattr__` / `__setattr__` route through the bag's declared schema for
     default/validation, then write back via `obj.attributes`.
   - Can be used standalone without `TypedAttr` (for incremental adoption by
     game code that already has the batched-blob pattern).

3. Usage example (documented in engine docstring and downstream
   `docs/performance.md`):
   ```python
   class Character(DefaultCharacter):
       # One JSONB path for all stats — one row for all of them
       stats = TypedAttr(backend="bag", version=1, keys={
           "strength":  AttrField(int, default=10, min=1, max=20),
           "dexterity": AttrField(int, default=10, min=1, max=20),
       })

       # Scalar in JSONB — queryable via GIN: Character.jsonb_filter(level=5)
       level = TypedAttr(int, default=1)

       # Real indexed column — btree query: Character.objects.filter(level__gt=5)
       # Requires: add IntegerField("level") to CharacterDB + migration
       level = TypedAttr(int, default=1, backend="real_col", db_index=True)

       # Existing AttributeProperty syntax still works unchanged
       biography = AttributeProperty(default="")
   ```

4. Query helper for JSONB `TypedAttr`:
   ```python
   # On the manager or as a classmethod:
   Character.jsonb_filter(faction="corp", level=10)
   # → ObjectDB.objects.filter(
   #       db_attrs__contains={"_": {"faction": "corp", "level": 10}}
   #   )
   ```

**Downstream changes:**
- Migrate game systems incrementally to `TypedAttr`. No forced migration. Old
  `.db.foo` still works. New systems declare `TypedAttr`; old systems leave
  `AttributeProperty` or plain `.db` in place.
- Systems that already use the batched-blob pattern (traits, buffs) migrate to
  `TypedAttr(backend="bag")` — drops custom handler boilerplate, gains schema
  validation.

**Done when:** `TypedAttr` all three backends implemented and tested; `AttributeBag`
standalone usable; `AttributeProperty` still works unchanged; query helper works.

---

### Phase 6 — Schema Versioning and Migration Runner

**Goal:** Declared attribute schemas can version-stamp objects and apply rename/
transform migrations automatically on first access after an upgrade.

**Engine changes:**

1. `_v` field in `db_attrs` document: integer, defaults to 0 for un-versioned
   objects (all existing objects after Phase 1 migration).

2. `SchemaVersion` class in `typed_attr.py`:
   ```python
   class RenameAttr:
       def __init__(self, old_key, new_key, category=None): ...
       def apply(self, attrs_dict): ...

   class TransformAttr:
       def __init__(self, key, fn, category=None): ...
       def apply(self, attrs_dict): ...

   class DropAttr:
       def __init__(self, key, category=None): ...
       def apply(self, attrs_dict): ...
   ```

3. `TypeclassBase` metaclass hook: on object load, if the typeclass declares
   `_attr_schema_version` and the object's `db_attrs["_v"]` is lower, run
   pending migrations:
   ```python
   class Character(DefaultCharacter):
       _attr_schema_version = 3
       _attr_migrations = {
           2: [RenameAttr("hit_points", "hp")],
           3: [RenameAttr("mana_points", "mp")],
       }
   ```
   Migration runs in `at_init()` or on first `.attributes` access. Writes
   updated `_v` back. Marks object dirty. Next flush persists to DB.

4. Management command `evennia migrate_attr_schemas [--typeclass path] [--dry-run]`:
   - Iterates all objects of a typeclass, applies pending migrations, bulk
     saves. For upgrading all objects in bulk offline rather than lazily on
     access.
   - `--dry-run` reports what would change without writing.

5. Detection of undeclared stale keys (dev mode only):
   - If `DEBUG=True`, on object load, log a warning for any key in `db_attrs`
     that is not declared in the typeclass's `_typed_attrs` registry. Helps
     catch leftover keys from old schema versions during development.

**Downstream changes:**
- For each subsystem that has ever changed an attribute key name: add a
  `_attr_schema_version` and corresponding `_attr_migrations` entry.
- Document the pattern in `docs/performance.md` under a new "Schema evolution"
  section.

**Done when:** Migration runner applies `RenameAttr` / `TransformAttr` / `DropAttr`
correctly; version bump on write confirmed; stale-key warning fires in DEBUG mode;
bulk migration command works end-to-end on a test dataset.

---

### Phase 7 — Polars Bulk Tick API

**Goal:** Game systems that iterate large numbers of objects can read and write
attribute state via vectorized Polars DataFrames rather than per-object Python
loops.

**Engine changes:**

1. `evennia/typeclasses/bulk.py`:

   ```python
   class BulkAttrQuery:
       """
       Read and write JSONB attrs for a set of objects in bulk.

       Usage:
           df = BulkAttrQuery.read(
               typeclass="typeclasses.characters.Character",
               attrs=["hp", "max_hp", "regen_rate"],
               extra_filters={"db_location__isnull": False},
           )
           # df is a polars.DataFrame with columns: id, hp, max_hp, regen_rate

           df = df.with_columns(
               (pl.col("hp") + pl.col("regen_rate"))
                 .clip(upper_bound=pl.col("max_hp"))
                 .alias("hp")
           )

           BulkAttrQuery.write(df, attrs=["hp"])
           # Issues one SQL UPDATE per changed row via bulk_update or COPY
       """

       @classmethod
       def read(cls, typeclass, attrs, category=None, extra_filters=None,
                real_cols=None) -> "polars.DataFrame": ...

       @classmethod
       def write(cls, df: "polars.DataFrame", attrs, category=None) -> int:
           """Write changed rows back. Returns count of rows updated."""
           ...
   ```

   `read` implementation:
   - Raw SQL with `json_extract_path_text` (PostgreSQL) to pull specific JSONB
     keys directly: `SELECT id, db_attrs->'_'->>'hp' as hp FROM objectdb WHERE ...`
   - Returns Polars DataFrame via `pl.from_records` or `pl.read_database`.
   - Does NOT go through the AttributeHandler or L1 cache — bypass is intentional
     for bulk paths. Marks affected objects as dirty in `_DIRTY_OBJECTS` after
     write so the L1 cache knows to reload on next per-object access.

   `write` implementation:
   - Collect changed rows (compare to read snapshot or let caller track deltas).
   - Build `jsonb_set` UPDATE or use PostgreSQL `unnest` bulk update.
   - After write: invalidate `_attrs_l1` for affected object IDs so stale L1
     cache doesn't serve old values.

2. Game-side integration points (downstream `mootest`):
   - `StaminaRegenScript.at_repeat()` → `BulkAttrQuery.read` + Polars vectorized
     calc + `BulkAttrQuery.write`.
   - `BleedingTickScript.at_repeat()` → same pattern.
   - Any `at_repeat` that currently loops `for obj in ObjectDB.objects.filter(...):`
     and reads `.db.foo` is a candidate.

3. Guard: if Polars not installed, `BulkAttrQuery.read` raises `ImportError` with
   install instructions. Non-bulk paths unaffected.

**Downstream changes:**
- Migrate hot global scripts to `BulkAttrQuery`. Prioritize by tick frequency × 
  object count.
- Update `docs/performance.md` with Polars tick pattern as the recommended
  approach for any `at_repeat` iterating > 50 objects.

**Done when:** `BulkAttrQuery.read` returns correct Polars DataFrame for a known
object set; `write` correctly persists to JSONB; L1 cache invalidated after write;
at least one game script migrated; benchmark shows speedup vs old loop.

---

### Phase 8 — Hot Column Promotion

**Goal:** Selected high-frequency, equality/range-queried attributes get real
indexed SQL columns for btree query performance that GIN cannot match.

This phase is intentionally **incremental and permanent**: promote only attributes
that justify the migration cost. Not a mass-migration of all attrs.

**Criteria for promotion to real column:**
- Queried in SQL with `=`, `>`, `<`, `IN`, or `ORDER BY` frequently.
- Not queried via JSONB GIN (which handles `@>` containment well but is slower
  for range queries and ordering).
- Stable enough to be worth a migration.

**Candidates from current game code:**
- `level` (int) — range queries, leaderboards.
- `faction` (str) — equality filter, group queries.
- `death_state` (str/bool) — frequent filter in tick scripts.
- `needs_chargen` (bool) — startup filter.

**Engine changes per promoted attribute:**

1. Developer adds `TypedAttr(int, backend="real_col")` to the typeclass.
2. Developer runs `evennia makemigrations` — Django generates the column add.
3. One-time backfill migration: populate new column from `db_attrs` for existing
   objects.
4. Engine `TypedAttr` descriptor routes get/set to the real column instead of
   JSONB.
5. Optional: remove the key from `db_attrs` after backfill (reduces JSONB size
   but adds complexity — leave in place unless storage is a concern).

**Engine scaffolding:** `TypedAttr(backend="real_col")` auto-detects on first
set whether the column exists and raises a clear error if the migration hasn't
been run yet, rather than silently falling back to JSONB.

**Done when:** At least `level` and `faction` promoted; queries on those columns
use btree index (confirm via `EXPLAIN ANALYZE`); existing JSONB queries still
work on all other attrs.

---

### Phase 9 — Cleanup and Documentation

**Goal:** Remove all dead code; update all docs; set up the forward-looking
design so ECS can slot in later without rewriting declarations.

**Engine changes:**

1. Remove from `attributes.py` (whatever remains after Phases 3–4):
   - `InMemoryAttributeBackend`
   - `IAttributeBackend`
   - All typed-column constants and helpers
   - `flush_if_pending` / `flush_all_dirty` (service.py integration already updated)

2. Update `evennia/.agents/prompts/A1-attribute-descriptors.md`:
   - Status: complete.
   - Document chosen design.

3. Update `evennia/.agents/docs/engine-api-architecture.md`:
   - A1 item: rewrite from "descriptors are canonical (P3)" to actual state.
   - Add backend-agnosticism note: `TypedAttr(backend=...)` parameter is the ECS
     slot-in point. A future `backend="component"` can route to a column store
     without changing any declaration sites.

4. Update `evennia/.agents/docs/engine-long-horizon.md`:
   - ECS horizon: note that `TypedAttr` backends are the integration point.
     `backend="component"` is a reserved keyword for the column-store path.

5. Remove `redis_attr_cache.py` entirely (stub from Phase 3 can go).

6. Remove `attribute_metrics.py` or repurpose for JSONB metrics.

7. Prometheus metrics update:
   - Remove: `flush_all_dirty` timing, attr row counts, dirty pending count.
   - Add: `jsonb_flush_objects_total`, `jsonb_flush_duration_seconds`,
     `jsonb_l1_cache_size`, `bulk_query_rows_read`, `bulk_query_rows_written`.

**Downstream changes:**
- Update `docs/performance.md`: replace all advice about "group attrs into one
  dict attribute" with `TypedAttr(backend="bag")`. Update section 1 (`db` vs
  `ndb`) to mention `db_session` semantics. Update anti-patterns list.
- Update `docs/architecture.md` A1 entry.
- Remove `ENGINE_HOT_ATTRIBUTE_KEYS` from `settings.py` (obsolete).
- Remove `ATTRIBUTE_STORAGE_BACKEND` from `settings.py` once old path is gone.

**Done when:** `clean_rot.py` exits 0; all tests pass; no references to removed
symbols; Prometheus dashboard updated.

---

## Dependency Graph

```
Phase 0 (deps + serialization utils)
    │
Phase 1 (schema: JSONB columns + backfill)
    │
Phase 2 (JSONB handler, parallel path, settings flag)
    │
Phase 3 (Redis L2 removal)          ← requires Phase 2 in prod and soak-tested
    │
Phase 4 (AttributeDB removal)       ← requires Phase 2 confirmed source of truth
    │
    ├── Phase 5 (TypedAttr descriptors)    ← can start after Phase 2
    │       │
    │       └── Phase 6 (schema versioning) ← requires TypedAttr
    │
    ├── Phase 7 (Polars bulk API)          ← can start after Phase 4
    │
    └── Phase 8 (hot column promotion)     ← can start after Phase 5
            │
            └── Phase 9 (cleanup + docs)  ← requires all prior phases
```

Phases 5 and 7 can run in parallel after Phase 4. Phase 8 depends on Phase 5
(needs `TypedAttr` declaration site). Phase 6 depends on Phase 5. Phase 9 is
last.

---

## Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| `populate_jsonb` data loss (Phase 1) | Medium | `check_attr_parity` command before Phase 4; keep `AttributeDB` table alive until parity confirmed |
| JSONB L1 cache stale after `BulkAttrQuery.write` (Phase 7) | Medium | Explicit L1 invalidation after bulk write; integration test that verifies stale cache is cleared |
| `db_session` not cleared on reload — ndb semantics broken | Medium | `clear_all_session` management command wired to `at_server_start`; test that session is empty after restart |
| GIN index slows writes noticeably | Low | GIN indexes on JSONB have overhead per-write; benchmark before/after; consider partial GIN (only index `"_"` key) if needed |
| `real_col` column missing migration causes silent JSONB fallback | Low | `TypedAttr(backend="real_col")` raises hard error if column absent — no silent fallback |
| Complex Python objects (class instances) round-trip incorrectly via msgpack | Medium | Comprehensive round-trip tests in Phase 0; flag any type that can't round-trip cleanly |
| Tags inadvertently broken by TypedObject model changes | Low | Tags remain M2M to `Tag` model, unchanged; explicit test coverage |

---

## Testing Requirements

Each phase must pass before the next starts:

- **Unit:** `evennia/typeclasses/tests/` — new test files per phase, existing
  tests updated as APIs change. Never remove a test that was passing before a
  phase started.
- **Integration:** `evennia test world` (downstream) must pass after each phase
  lands.
- **Parity test (Phase 1–2):** write a test that creates 50 objects, sets varied
  attrs via old handler, populates JSONB, reads via new handler, asserts equal.
- **Flush test (Phase 2):** verify that N attr changes on one object result in
  exactly 1 DB write per flush tick.
- **Benchmark (Phase 7):** record tick duration before and after Polars migration
  for at least one global script. Commit the benchmark output as a comment in
  the script file.

---

## Commit and EVENNIA_REF Discipline

Each phase is one PR to the engine fork and one companion PR to the downstream
repo (if any game-side changes are required). Never merge the game-side PR before
the engine PR. Bump `EVENNIA_REF` in `.github/workflows/deploy.yml` in the same
engine PR that introduces the new behavior, not after.

Phases 3 and 4 (removal) must be deployed to production with the new JSONB
handler confirmed live before the removal PR merges. If production ever needs
to roll back past Phase 2, the `AttributeDB` table must still exist — do not
drop it until the JSONB path has been in production for a minimum of 2 weeks.

---

## Files Touched Summary

| File | Change |
|---|---|
| `evennia/typeclasses/attributes.py` | Progressive removal; `AttributeProperty` adapted |
| `evennia/typeclasses/models.py` | Add `db_attrs`, `db_session` JSONFields; remove `db_attributes` M2M |
| `evennia/typeclasses/tags.py` | Unchanged |
| `evennia/typeclasses/redis_attr_cache.py` | Removed (Phase 3) |
| `evennia/typeclasses/attribute_metrics.py` | Updated or removed |
| `evennia/typeclasses/jsonb_util.py` | **New** (Phase 0) |
| `evennia/typeclasses/jsonb_query.py` | **New** (Phase 0) |
| `evennia/typeclasses/jsonb_handler.py` | **New** (Phase 2) |
| `evennia/typeclasses/typed_attr.py` | **New** (Phase 5) |
| `evennia/typeclasses/bulk.py` | **New** (Phase 7) |
| `evennia/typeclasses/migrations/0023_*.py` | **New** (Phase 1) |
| `evennia/typeclasses/migrations/0024_*.py` | **New** (Phase 1) |
| `evennia/typeclasses/migrations/0025_*.py` | **New** (Phase 4) |
| `evennia/server/service.py` | Flush handler swap |
| `evennia/server/prometheus_metrics.py` | Metrics updated |
| `evennia/.agents/docs/engine-api-architecture.md` | A1 entry updated |
| `evennia/.agents/docs/engine-long-horizon.md` | ECS horizon updated |
| `mootest/server/conf/settings.py` | Remove attr cache settings |
| `mootest/docs/performance.md` | Updated throughout |
| `mootest/docs/architecture.md` | A1 entry updated |
