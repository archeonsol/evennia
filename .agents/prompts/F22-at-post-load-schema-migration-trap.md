# F22: at_post_load schema-migration is dead for every typeclass

Status: todo (decision deferred)

Surfaced reviewing the EvEditor input-capture migration
([CM1-input-capture-migration.md](CM1-input-capture-migration.md)), which had to
wire a reload-rehydration seam into `at_post_load` and discovered the hook is
shadowed everywhere.

## The finding

The engine ships a declarative **attribute schema-migration** facility:
`apply_schema_migrations(obj)` in
[`evennia/typeclasses/typed_attr.py`](../../evennia/typeclasses/typed_attr.py).
A typeclass opts in by declaring `_attr_schema_version` + `_attr_migrations`, and
pending migrations are meant to apply automatically on load. Its own docstring
example is `class Character(DefaultCharacter): _attr_schema_version = 2`.

It is called from exactly one place: `TypedObject.at_post_load`
([`evennia/typeclasses/models.py`](../../evennia/typeclasses/models.py) ~580).
But **every concrete typeclass overrides `at_post_load` without calling
`super()`**, so the call never runs:

- `LifecycleMixin.at_post_load` (all Objects/Characters/Rooms) — was bare `pass`;
  now installs the capture-rehydration seam (still no `super()`).
- `DefaultAccount.at_post_load` — same.
- `DefaultScript.at_post_load`, `Channel.at_post_load` (comms) — bare `pass`.
- `Exit.at_post_load` — does `cmdset.remove_default()`, no `super()`.
- `bots.py` — `_load_channels()`, no `super()`.

So `apply_schema_migrations` is **dead code for every built-in typeclass**.

Why nothing is broken *today*: zero engine typeclasses declare
`_attr_schema_version` (grep is empty), so the dead call is a no-op anyway. The
unit tests in
[`test_typed_attr.py`](../../evennia/typeclasses/tests/test_typed_attr.py) call
`apply_schema_migrations` **directly** — they prove the function's logic but
never exercise the `at_post_load` wiring. Classic verification trap: green ≠
working. A game that follows the docstring and declares `_attr_schema_version` on
a Character would get a **silent no-op** with no error.

Adjacent fact (not necessarily a bug): because the shadow also drops the base
call, objects/accounts don't run *any* base `at_post_load` behavior. The
capture-rehydration seam works only because it was wired into the *effective*
(shadowing) hooks, not the base.

## The two questions to decide (do not pre-judge)

**Q1 — what to do with `apply_schema_migrations`.** Options:
1. **Make it fire.** Have the shadowing overrides call `super().at_post_load()`
   (or move the call into a place every typeclass reaches). Risk: this silently
   *enables* a never-run code path on every object load for all games at once;
   needs an end-to-end test through `at_post_load` (the missing coverage) and a
   per-load cost check (mirror the F22-sibling concern that the EvEditor seam
   already navigated with a cache-free attribute probe).
2. **Delete it.** If the backfill pattern (Q2) is the chosen migration story,
   the per-object lazy facility may be dead weight. Removing it also retires a
   documented-but-broken feature rather than fixing it.
3. **Document the limitation.** Keep the function for direct/manual use, but
   state in the docstring that it does *not* auto-run for objects/accounts and
   point at the backfill pattern.

**Q2 — engine vs game backfill.** The game (`../newmoo`) already solves data
migration with a clean pattern the engine lacks: one-time **backfill scripts run
at `at_server_start`**, each guarded by a `ServerConfig` flag for idempotency,
logged at startup (`_backfill_medical_state_tags`, `_backfill_discord_link_tags`,
…). Decide whether this is worth promoting into the engine as agnostic infra:
- **Promote**: a generic engine "run-once-per-version backfill" runner
  (ServerConfig-guarded, idempotent, logged) — a vendor-neutral primitive any
  game could use, replacing both ad-hoc game backfills *and* possibly the dead
  `apply_schema_migrations`.
- **Leave in game**: per [Core Beliefs](../../.agents/docs/core-beliefs.md) the
  engine hosts only *agnostic wins*. A backfill runner may be too game-shaped
  (what/when to migrate is game policy). If so, the engine answer is just Q1.3
  (document) and the pattern stays a game idiom.

Q1 and Q2 overlap: both answer "how does persisted data migrate on a version
bump." A coherent decision picks one migration story, not two half-built ones.

## Approach

**Discussion first — this is a decision prompt, not a build spec.** No code until
the direction is chosen.

1. Confirm the finding still holds (grep `_attr_schema_version`; check the
   `at_post_load` override list above; confirm the tests don't route through the
   hook).
2. Read newmoo's `server/conf/at_server_startstop.py` backfill block + its
   `ServerConfig` guards as the concrete prior art for Q2.
3. Weigh Q1 × Q2 against the engine/game split. Present a recommendation with
   tradeoffs; get a decision before building.
4. Whatever is chosen, the **missing end-to-end coverage** (a migration that
   actually fires via `at_post_load` / the chosen trigger) is the thing to build
   first if Q1.1 wins — it's also the test that proves the fix.

## Scope boundary

- **In scope**: the `apply_schema_migrations` ↔ `at_post_load` wiring, the
  shadowing across the named typeclasses, and the engine-vs-game backfill
  decision.
- **Out of scope**: a broader `at_post_load`/hook-MRO refactor; changing what
  the shadowing overrides *do* (they each have a real reason); the A1 attribute
  storage rework ([A1-attribute-descriptors.md](A1-attribute-descriptors.md)),
  though a decision here should be consistent with it.

## Done means

- Q1 decided and acted on: fire (with the missing end-to-end test + cost check),
  delete, or document — explicit in the commit message.
- Q2 decided: a generic engine backfill runner exists, or a written rationale for
  why backfill stays game-side.
- No documented-but-dead feature left behind: whatever survives is either proven
  to run or its limitation is stated where a reader would hit it.

## Repo conventions

See [AGENTS.md](../../AGENTS.md). TDD; the end-to-end test is the deliverable that
proves Q1.

## Ask before

- Making `apply_schema_migrations` fire for all typeclasses (enables a never-run
  path everywhere — needs the test + cost sign-off first).
- Adding a new engine primitive for backfill (engine/game split call).
- Touching the shadowing overrides' existing behavior.
