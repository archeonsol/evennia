# Prompts: Level 2 architecture work

Self-contained prompts for picking up Level 2 architecture items in a
fresh agent context. Each prompt assumes no prior conversation
history: drop it into a new context and the agent has everything
needed to start.

## How to use

1. Pick a prompt from the list below.
2. Open a fresh agent context.
3. Paste the prompt content as the initial message.
4. The agent proposes a design first, gets review, then implements.

## Prompts in this folder

Currently parallel-startable (no unresolved dependencies):

**No dependencies on anything not yet shipped:**

- [A1: attribute storage model](A1-attribute-descriptors.md) —
  large, exploratory. Direction not yet committed (typed descriptors
  are one candidate, not the foregone answer).
- [I1: actor abstraction](I1-actor-abstraction.md) — substrate;
  unblocks L1, I2, R1. **Scope under review:** CM1 Phase 3a already
  shipped an `Actor` in `evennia/actions/actor.py` (unifies the
  session/account/character trio for the action engine). Whether that
  delivers I1 or leaves a distinct remaining scope (legacy `self.caller`
  migration, L1/R1/I2 consuming that `Actor`) is unresolved; the
  architecture-doc I1 entry is stale on this. Confirm before starting.
- [AS2: unified system scheduler](AS2-system-scheduler.md) — large,
  not started; design approved, prompt is the build spec. One engine
  primitive replacing the game's `global_tick` / `Script.interval` /
  APScheduler. Companion downstream migration is game-repo work.

**Shipped:**

- [AS1: sync/async commitment](AS1-implementation-roadmap.md) — engine
  shipped in `6.0.0+underspire.50` (`evennia.utils.defer` + reactor-stall
  watchdog). Phase 2 (downstream blocking-site migration) is game-repo
  work, tracked there. Settles the rule-body contract CM1 builds on.

**CM1 action system (active; replaces the old cmdset rethink):**

- [CM1: action-system roadmap](CM1-action-system-roadmap.md) —
  authoritative design + status. Phase 1–8 complete: the engine action
  bridge is the sole player-input dispatch path, player cmdsets are empty
  anchors, the legacy `commands/*_cmds` tree is deleted. Remaining is
  optional substrate archival (retire `evennia/commands/cmdset.py` /
  `CmdSet` from `evennia/__init__.py`).
- [CM1: cmdset-elimination port ledger](CM1-port-ledger.md) — the live
  burndown tracker for the roadmap; update it in the same commit as each
  port batch.
- [CM1: input-capture migration](CM1-input-capture-migration.md) —
  in-progress. Follow-on to the `.73` engine-only-dispatch release.
  EvMore (`.77`) and **EvEditor** are done — both on engine
  `StateProvider`s, modeled on `.73`'s `GetInputState`/`YesNoState`, with
  a shared reload-rehydration seam. Remaining: **EvMenu** migration (then
  it appends its row to `_CAPTURE_REHYDRATORS`), any other cmdset
  `CMD_NOMATCH`/`CMD_NOINPUT` capture, and finally removing the legacy
  cmdset dispatch path (gated on `cmdobj=` rehoming + sign-off).

**Alpha-promotion audit (read-only whole-engine audit, 2026-06-06):**

Output of a verified 6-system audit (smells, incomplete refactors, shims,
dead code, stale migrations, inefficiency) gating pre-alpha → alpha. Listed in
**recommended execution order**: independent low-risk cleanups first, then the
cross-repo decisions, then the dependent removals, squash last. 1-3 are
parallel-startable now.

1. [ALPHA: dead-code batch](ALPHA-dead-code-batch.md) — **re-verified 2026-06-07,
   list had drifted.** Tier 1 is the safe removal set (run_async, orphaned
   session methods, init_new_account, _next_task_id, nomatch alias,
   remove_attributes_on_delete, south branch, clean_senddata residue,
   cumulative_rank_mask). Tier 2 claims are stale, do NOT remove (from_lockstring/
   LegacyLock and the middleware/noinput path are live; the Redis L2 cache is
   already gone). Tier 3 (webclient legacy, context account branch, base
   _get_cache_key) are behavior changes needing their own trace/test. Independent.
2. [ALPHA: shim + except cleanup](ALPHA-shim-except-cleanup.md) — low-risk and
   independent. Remove AMP/ondemand pickle shims (security: pickle on the wire),
   the `get_objs_with_attr` shim, and four bug-hiding `except: pass` cache/metrics
   sites.
3. [ALPHA: ssh.py portal boundary](ALPHA-ssh-portal-boundary.md) — self-contained
   decision: the one ORM bleed into the Portal process; remove SSH or delegate
   auth over AMP.
4. [ALPHA: jobs/ + event bus boundary](ALPHA-jobs-eventbus-boundary.md) —
   cross-repo decision; settle before the squash (it owns the `server/0004`
   model). Two fully-built-but-unconsumed subsystems + an `evennia.events` vs
   `evennia.actions.events` name collision. Wire (machinery in engine, usage in
   game) or cut.
5. [ALPHA: login engine ownership](ALPHA-login-engine-ownership.md) — cross-repo;
   must land before cmdset retirement can delete the `cmdobj=` login path. Move
   login fully into the engine (a promote-to-engine smell); kill the phantom
   `LoginSessionMixin` comment; unify the web/REST path.
6. [ALPHA: cmdset retirement audit](ALPHA-cmdset-retirement-audit.md) — the CM1
   finish line; after the in-flight EvMenu removal **and** #5. Audit every cmdset
   consumer, then delete the cmdset machinery (`CmdSet`, handler, parser,
   syscommands, `CMD_*`, anchors, the dead `commands/default/` tree).
- [ALPHA: engine minimal-set inventory](ALPHA-engine-minimal-inventory.md) —
   reference, not a task. The "what's actually left" companion to #6: confirms the
   engine modules are clean and the dead `commands/default/` tree is the only large
   removable mass. Read before re-deriving whether game-shaped code hides in `evennia/`.
7. [ALPHA: migration squash](ALPHA-migration-squash.md) — last; meticulous,
   cross-repo. Coordinate after #4. Per-app squash plan; hazards: scripts/0019
   reads the deleted Attribute model, GIN ops must stay `atomic=False`,
   typeclasses squashes last.
8. [ALPHA: engine ↔ game cross-review](ALPHA-engine-game-cross-review.md) — stub;
   after the known items above land. Bidirectional pass the repo-locked audit
   couldn't do: engine surfaces the game never uses, and game logic that should
   be promoted to the engine. Produces follow-up prompts of its own.

**Re-decide (parked questions with new signal):**

- [Engine metrics / observability surface](engine-metrics-surface.md) — **resolved
  (shipped):** committed to the Prometheus surface already in the tree
  (`server/prometheus_metrics.py`, `/metrics`, gated by
  `ENGINE_PROMETHEUS_METRICS_ENABLED`); removed the redundant swallow at the flush
  site, collapsed the forwarder, wired the dead backlog-warn helper. (The
  engine/game boundary migration is complete; its outcome is recorded in
  [`engine-architecture/decisions.md`](../docs/engine-architecture/decisions.md).)

Each prompt invites the agent to propose a design before
implementing. The user reviews the design before the agent starts
coding. This is approach (a) from the prompt-design discussion: don't
commit to a design in the prompt, let the executing agent propose one
cold and iterate with the user.

## Status convention

Mark status at the top of each prompt as work progresses:

- `Status: todo` (default)
- `Status: in-progress`
- `Status: shipped (commit SHA)`

## Hygiene prompts

Smaller, more focused items than the Level 2 architecture work
above. Each one is independent; pick whichever has appetite.
F-numbers are stable IDs carried over from the retired hygiene
backlog.

**Discussion-first** (a policy decision before any edits):

- [F13: contribs half-policy](F13-contribs-policy.md)
- [F18: unused engine handlers](F18-unused-engine-handlers.md)
- [F20: ownership-change provenance](F20-ownership-provenance-audit.md)
  — follow-on to the `.71` ControlBinding rework; a cold-path audit
  trail for past ownership, engine-vs-game placement to decide.
- [F22: at_post_load schema-migration trap](F22-at-post-load-schema-migration-trap.md)
  — surfaced by the EvEditor capture migration. `apply_schema_migrations`
  is dead for every typeclass (every `at_post_load` override shadows the
  base without `super()`); decide fix/delete/document, plus whether to
  promote newmoo's startup-backfill pattern into the engine. Overlaps
  F21's backfill-machinery sweep.
- [F23: dead `COMMAND_DEFAULT_CLASS` test patches](F23-dead-command-default-class-patches.md)
  — surfaced by F17. Nine stacked `@patch` decorators on
  `BaseEvenniaCommandTest` are doubly dead (stale `evennia.commands.account`
  path + a base class with no test methods, so they never start). Decide
  fix-vs-delete; fixing would silently activate 9 dormant patches.

**Mechanical / verification:**

- [F3: doc rot sweep in `docs/source/`](F3-doc-rot-sweep.md) —
  gated on a newmoo PR.
- [F17: `@patch("dotted.path")` audit](F17-patch-dotted-path-audit.md) —
  **shipped** (`underspire.85`); 84 sites rewritten, dead remainder carved to F23.
- [F21: migration-scaffolding carveout](F21-migration-scaffolding-carveout.md)
  — in-progress. The I1 reconcile scaffolding was removed in `.72`; the
  broader sweep for one-time backfill machinery remains.

**Audit (read before acting):**

- [F9: big modules audit](F9-big-modules-audit.md)
- [audits-deferred](audits-deferred.md) — umbrella list of named
  audits without enough signal to start yet.

## Cross-references

- [Engine Architecture](../docs/engine-architecture/index.md) —
  decisions record; canonical definition of every item here
  ([decisions](../docs/engine-architecture/decisions.md) shipped,
  [committed](../docs/engine-architecture/committed.md) not-yet-built,
  [horizon](../docs/engine-architecture/horizon.md) speculative).
- [Core Beliefs](../docs/core-beliefs.md) — the engine/game placement rule.
- [Typeclass Hooks Contracts](../../docs/source/Components/Typeclass-Hooks.md) +
  [Reference Tables](../../docs/source/Components/Typeclass-Hooks-Reference.md) — B1's
  output; consumed by H1, M1, R1, CM1.
