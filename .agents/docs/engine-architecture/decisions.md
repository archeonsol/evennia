# Decisions (shipped)

Decided and in the tree. Each entry: the choice, why, and where it lives. Not
yet built: [committed.md](committed.md); speculative: [horizon.md](horizon.md).

## Action engine replaces cmdsets (CM1)

The merge-time-and-cached cmdset model was too hard to reason about (priority,
merge type, key collisions, cache invalidation). **Decision:** a new action
engine in `evennia/actions/` is the **sole** player-input dispatch path; player
cmdsets are empty anchors. Input routes through `try_action_dispatch` with
explicit rules/predicates instead of cmdset merges. The legacy cmdset machinery
is on the way out: [cmdset retirement prompt](../../prompts/ALPHA-cmdset-retirement-audit.md).

## Actor abstraction (I1)

`self.caller` could be Session, Account, or Object depending on routing; the
session-proxy / `AccountCommand` machinery papered over it. **Decision:** `Actor`
(`evennia/actions/actor.py`) is a computed view over the (session, account,
character) triple exposing `identity`/`focus`/`effective`/`location`, built by
`Actor.from_caller`; every dispatch resolves through it. Closeout pending:
`AccountCommand` deprecated, residual `self.caller` confined to dying legacy
command modules, the `actor.character` vs `actor.effective` seam a known nit.

## JSONB attribute storage

"Everything is an Attribute" meant one DB row per value (query volume scaling
with field count) and pickled values unfilterable in SQL. **Decision:** the M2M
`Attribute` model is deleted; values live in a `db_attrs` JSONField with GIN
indexes, behind an `IAttributeBackend` interface (`typeclasses/attributes.py`)
with JSONB and InMemory backends. The remaining open part of the old A1
question (a typed attribute *API*) is not committed.

## Partial loads of idmapper models are unsupported

`.only()`/`.defer()` cannot build an uncached `SharedMemoryModel`: construction
reads fields beyond any partial row (`TypedObject.__init__`, `at_post_load`),
and idmapper can never refresh a deferred field (the refresh query returns the
same cached instance without applying values, then `KeyError`). On a *cached*
object a partial query returns the cached instance itself, so `from_db` treats
missing fields as "no information", never "reset" (see `ObjectDB.from_db`).
**Decision:** engine code reads raw columns via `values()`/`values_list()` and
writes back via `queryset.update()`; `BulkTickContext` is the reference.

## Typed search result (Q1)

**Decision:** `caller.search_for(...) -> Found | Ambiguous | NotFound` (pure,
no side effects; types in `evennia/objects/search_result.py`), with
`caller.search(...) -> Object | None` as sugar over it. `quiet` removed;
`not_found` / `ambiguous` kwargs replace the old `*_string` names. The
`at_search_result` hook signature is unchanged.

## Sync by default + threaded I/O helpers (AS1)

Long-running hooks stalled the reactor with no declared I/O story. **Decision:**
sync hooks, plus `evennia/utils/defer.py` (`in_thread`/`background`/`threaded`)
for blocking work and a reactor-stall watchdog; `defer.inlineCallbacks` is no
longer recommended. Downstream blocking-site migration is game-repo work.

## Unified system scheduler (AS2)

Recurring work had four answers (`global_tick`, `Script.interval`, a racing
APScheduler daemon thread, TickerHandler). **Decision:** one primitive,
`evennia/utils/systems.py`: declared cadence x scope, reactor bodies once per fire,
overlap-guarded via `SYSTEM_MODULES`; per-object runtime timers gone (systems-plus-clock
half of a future ECS). Both tranches shipped (A: TickerHandler `.89`; B: `Script.interval`
machinery + timer columns + all consumers, `.93` — **Script is now storage-only**).

## Hook registry (H1)

Hook signatures were inconsistent and undiscoverable. **Decision:** every public
engine hook declares `@hook(...)` with event/phase/actor/returns/discipline
metadata; startup lints that base classes' `at_*`/`get_*`/`return_*` methods are
registered. The registry is **descriptive + validating metadata, not a
dispatcher** (the engine still calls `obj.at_pre_move(...)` directly). Flat API
at `evennia.hooks`; a doc generator emits the contract tables; game-side
overrides inherit registration silently.

## ControlBinding focus stack (identity / puppet)

**Decision:** a durable `ControlBinding` with a focus stack owns the
session→puppet relationship, replacing the legacy puid/puppet pointer pair. The
`Actor`'s `focus`/`identity` resolve from the binding when one is attached
(legacy triple is the bindingless fallback). This is the substrate for
re-evaluating the multi-puppet question (I2; see [committed.md](committed.md)).

## Narrative emote render/deliver seam

**Decision:** emote output goes through a structured `EmotePlan -> deliver() ->
EmoteResult` path with pluggable linguistics filters (`evennia/narrative/`). This
is the first shipped instance of the "render before deliver" principle.

## Universal render/deliver pipeline (R1)

**Decision:** immutable `render.v1` nodes are universal output; `msg` is sugar,
telnet flattens, and Azaban/sinks retain semantics.

## Typed settings (S1) — dropped

**Dropped** (2026-05-30): typing ~290 settings / ~1000 reads was high churn; prefer narrow overrides.

## Capability authorization (R3A-R3E)

**Decision:** namespaced capabilities plus positive scoped grants replace tiers and
creator ownership. Structured policies, split caches, and a finite lock compiler/
freeze migration ship through R3E; see [r3-authorization.md](r3-authorization.md).

## Engine/game boundary migration — complete

The program of pushing fork-owned commands/helpers upstream is **done** (language
polish, flat-API hygiene, invalidation contracts, `at_sync` reattach hooks, the
typeclass-hooks taxonomy that became H1). Follow/escort/shadow: the engine ships
the agnostic mechanism (`Move` + `Locomotion` + `Departed`/`Arrived` events in
`evennia/actions/default/movement.py`); the commands are game policy.
Scene/IC broadcast: `evennia/narrative/delivery.py` does per-viewer broadcast
with sdesc/recog resolution; games override via a custom `EmoteDelivery`.
**Placement rule** (the enduring takeaway) lives in
[`core-beliefs.md`](../core-beliefs.md): the framing test (could a second
consumer re-implement it? if not, it's engine) plus agnostic-by-opt-out;
empty-default hooks engine-side, opinion downstream, English cleaned as touched.
