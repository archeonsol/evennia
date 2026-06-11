# Decisions (shipped)

Decided and in the tree. Each entry: the choice, why, and where it lives. For
not-yet-built work see [committed.md](committed.md); for speculative shapes see
[horizon.md](horizon.md).

## Action engine replaces cmdsets (CM1)

The merge-time-and-cached cmdset model was too hard to reason about (priority,
duplicates, merge type, key collisions, cache-invalidation bugs). **Decision:** a
new action engine in `evennia/actions/` is the **sole** player-input dispatch
path; player cmdsets are empty anchors. Input routes through `try_action_dispatch`
with explicit rules/predicates instead of cmdset merges. The legacy cmdset
machinery is on the way out (see the [cmdset retirement
prompt](../../prompts/ALPHA-cmdset-retirement-audit.md)).

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
is a narrow, shipped instance of the P2 "render before deliver" principle and the
first seam of the eventual R1 pipeline.

## Typed settings (S1) — dropped

Considered and **dropped** (2026-05-30). A typed class hierarchy over ~290
settings / ~1000 read sites was large churn for modest gain: most settings are
server config where scoping is meaningless, `OptionHandler` covers per-account
preferences, and the few settings wanting runtime scoping don't justify a
framework. If a need surfaces, prefer a narrow per-setting override.

## Alpha-hardening pass

**Decision:** before promoting the engine from pre-alpha to alpha, run a
read-only whole-engine audit and burn down the survivors (dead code, shims,
stale migrations, `except: pass` sites, speculative dead subsystems) as
one-prompt-per-task; it makes the existing surface clean, small, and honest.
Tracked in the [alpha-promotion prompts](../../prompts/README.md).

## Engine/game boundary migration — complete

The program of pushing fork-owned commands/helpers upstream is **done** (language
polish, flat-API hygiene, invalidation contracts, `at_sync` reattach hooks, the
typeclass-hooks taxonomy that became H1). The two lingering items, resolved since:

- **Follow / escort / shadow** — the engine ships the agnostic mechanism (the
  `Move` action + `Locomotion` route-follower + `Departed`/`Arrived` events in
  `evennia/actions/default/movement.py`); the specific commands are game policy
  built on those events.
- **Scene / IC broadcast** — `evennia/narrative/delivery.py` does per-viewer
  scene broadcast with sdesc/recog name resolution; games override via a custom
  `EmoteDelivery` (no named `room_ic_viewers` hook committed).

**Placement rule** (the enduring takeaway) lives in
[`core-beliefs.md`](../core-beliefs.md): the framing test (could a second
consumer re-implement it? if not, it's engine) plus agnostic-by-opt-out;
empty-default hooks engine-side, opinion downstream, English cleaned as touched.
