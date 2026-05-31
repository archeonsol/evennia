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

- [AS1: sync/async commitment](AS1-sync-async-commitment.md) —
  medium. Direction + helpers.
- [A1: typed attribute descriptors](A1-attribute-descriptors.md) —
  large.
- [I1: actor abstraction](I1-actor-abstraction.md) — largest.
  Substrate; unblocks L1, I2, R1.

**Unblocked by B1 (typeclass hooks taxonomy doc, shipped):**

- [M1: composable move_to](M1-composable-move.md) — medium. Builder
  replaces kwargs-as-API.
- [CM1: cmdset rethink](CM1-cmdset-rethink.md) — large. Replaces
  merge-time-and-cached with a simpler model; bakes introspection
  in from the start.

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
- [F20: prototype `exec` key](F20-prototype-exec-key.md)

**Mechanical / verification:**

- [F3: doc rot sweep in `docs/source/`](F3-doc-rot-sweep.md) —
  gated on a newmoo PR.
- [F6: `CMD_*` → `COMMAND_*` settings rename](F6-cmd-command-settings-rename.md)
- [F7: cmdset refactor open verifications](F7-cmdset-refactor-verifications.md)
- [F17: `@patch("dotted.path")` audit](F17-patch-dotted-path-audit.md)
- [F19: unused Django apps](F19-unused-django-apps.md)
- [F21: dead `_menutree` branch in fieldfill](F21-dead-menutree-branch.md)

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
