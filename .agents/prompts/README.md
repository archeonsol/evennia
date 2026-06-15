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

**Shipped:**

- [AS1: sync/async commitment](AS1-implementation-roadmap.md) — engine
  shipped in `6.0.0+underspire.50` (`evennia.utils.defer` + reactor-stall
  watchdog). Phase 2 (downstream blocking-site migration) is game-repo
  work, tracked there. Settles the rule-body contract CM1 builds on.

**Shipped (prompts removed; see git log):** AS2 unified system scheduler (both
tranches, `.89`/`.93`; rationale in
[`engine-architecture/decisions.md`](../docs/engine-architecture/decisions.md)),
engine metrics/observability surface.

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
  a shared reload-rehydration seam. EvMenu was deleted in `.85`, not
  migrated (the `_CAPTURE_REHYDRATORS` seam shipped with only the eveditor
  row). Remaining: any other cmdset `CMD_NOMATCH`/`CMD_NOINPUT` capture,
  then removing the legacy cmdset dispatch path (gated on `cmdobj=`
  rehoming + sign-off).

**Alpha-promotion audit (read-only whole-engine audit, 2026-06-06):**

Output of a verified 6-system audit (smells, incomplete refactors, shims,
dead code, stale migrations, inefficiency) gating pre-alpha → alpha. Listed in
**recommended execution order**: independent decisions first, then the dependent
removals, squash last. Items 1-2 are parallel-startable now.

**Shipped (prompts removed; see git log):** ALPHA dead-code batch (Tier 1),
shim + except cleanup, login engine ownership (`.95`).

1. [RECONSIDER: ssh.py portal boundary](ALPHA-ssh-portal-boundary.md) — **deferred**
   (2026-06-13): the ORM bleed is latent, not active (SSH defaults off, `ssh.py`
   never imported), so no live problem. Concrete benefit is dead-code removal;
   the boundary-guard test is principle-driven. Revisit only on a trigger in the
   prompt.
2. [ALPHA: jobs/ + event bus boundary](ALPHA-jobs-eventbus-boundary.md) —
   cross-repo decision; settle before the squash (it owns the `server/0004`
   model). Two fully-built-but-unconsumed subsystems + an `evennia.events` vs
   `evennia.actions.events` name collision. Wire (machinery in engine, usage in
   game) or cut.
3. [ALPHA: cmdset retirement audit](ALPHA-cmdset-retirement-audit.md) — the CM1
   finish line, **now unblocked**: its gates (EvMore/EvEditor capture migration,
   EvMenu removal in `.85`, login engine ownership in `.95`) are all cleared.
   Audit every cmdset consumer, then delete the cmdset machinery (`CmdSet`,
   handler, parser, syscommands, `CMD_*`, anchors, the dead `commands/default/`
   tree).
- [ALPHA: engine minimal-set inventory](ALPHA-engine-minimal-inventory.md) —
   reference, not a task. The "what's actually left" companion to the cmdset
   retirement audit: confirms the engine modules are clean and the dead
   `commands/default/` tree is the only large removable mass. Read before
   re-deriving whether game-shaped code hides in `evennia/`.
4. [ALPHA: migration squash](ALPHA-migration-squash.md) — last; meticulous,
   cross-repo. Coordinate after the jobs/event-bus decision. Per-app squash plan;
   hazards: scripts/0019 reads the deleted Attribute model, GIN ops must stay
   `atomic=False`, typeclasses squashes last.
5. [ALPHA: engine ↔ game cross-review](ALPHA-engine-game-cross-review.md) — stub;
   after the known items above land. Bidirectional pass the repo-locked audit
   couldn't do: engine surfaces the game never uses, and game logic that should
   be promoted to the engine. Produces follow-up prompts of its own.

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

**Shipped (prompts removed; see git log):** F17 patch-path audit (`.85`),
F18 unused engine handlers, F23 dead `COMMAND_DEFAULT_CLASS` patches.

**Discussion-first** (a policy decision before any edits):

- [F13: contribs half-policy](F13-contribs-policy.md)
- [engine i18n policy](engine-i18n-policy.md) — surfaced by the `.95` login
  review. The action-engine layer emits player text unwrapped, but two files
  (`dispatch.py`, `nomatch.py`) use `gettext`. Decide translatable vs.
  English-only and make the layer consistent.
- [F20: ownership-change provenance](F20-ownership-provenance-audit.md)
  — follow-on to the `.71` ControlBinding rework; a cold-path audit
  trail for past ownership, engine-vs-game placement to decide.
- [F22: at_post_load schema-migration trap](F22-at-post-load-schema-migration-trap.md)
  — surfaced by the EvEditor capture migration. `apply_schema_migrations`
  is dead for every typeclass (every `at_post_load` override shadows the
  base without `super()`); decide fix/delete/document, plus whether to
  promote newmoo's startup-backfill pattern into the engine. Overlaps
  F21's backfill-machinery sweep.

**Mechanical / verification:**

- [F3: doc rot sweep in `docs/source/`](F3-doc-rot-sweep.md) —
  gated on a newmoo PR.
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
