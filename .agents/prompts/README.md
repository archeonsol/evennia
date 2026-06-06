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
  medium, todo. Follow-on to the `.73` engine-only-dispatch release.
  Migrate EvMore / EvEditor (and any remaining cmdset
  `CMD_NOMATCH`/`CMD_NOINPUT` capture) onto engine `StateProvider`s,
  modeled on `.73`'s `GetInputState`/`YesNoState`. These are silently
  bypassed by the engine bridge today; their func-direct tests don't
  prove routing. Clears the blockers for removing the legacy cmdset
  dispatch path.

**Boundary work (depends on architecture-doc substrate):**

- [C1 + C2: boundary work finalization](C1-C2-boundary-finalization.md)
  — small. Last two items in
  [`engine-boundary-migration.md`](../docs/engine-boundary-migration.md).
  C1 depends on M1; C2 depends on R1. Prompt includes a dependency
  check to confirm path before starting.

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
- [F19: tag-search facade gaps](F19-tag-search-facade-gaps.md)
- [F20: ownership-change provenance](F20-ownership-provenance-audit.md)
  — follow-on to the `.71` ControlBinding rework; a cold-path audit
  trail for past ownership, engine-vs-game placement to decide.

**Mechanical / verification:**

- [F3: doc rot sweep in `docs/source/`](F3-doc-rot-sweep.md) —
  gated on a newmoo PR.
- [F17: `@patch("dotted.path")` audit](F17-patch-dotted-path-audit.md)
- [F21: migration-scaffolding carveout](F21-migration-scaffolding-carveout.md)
  — in-progress. The I1 reconcile scaffolding was removed in `.72`; the
  broader sweep for one-time backfill machinery remains.

**Audit (read before acting):**

- [F9: big modules audit](F9-big-modules-audit.md)
- [audits-deferred](audits-deferred.md) — umbrella list of named
  audits without enough signal to start yet.

## Cross-references

- [Engine API Architecture](../docs/engine-api-architecture.md) —
  Level 2 target; canonical definition of every item here.
- [Engine Boundary Migration](../docs/engine-boundary-migration.md) —
  fork/engine boundary work (different doc, different scope).
- [Engine Long-Horizon Sketch](../docs/engine-long-horizon.md) —
  Level 3 sketch (post-Level 2; not parallelizable yet).
- [Typeclass Hooks Contracts](../../docs/source/Components/Typeclass-Hooks.md) +
  [Reference Tables](../../docs/source/Components/Typeclass-Hooks-Reference.md) — B1's
  output; consumed by H1, M1, R1, CM1.
