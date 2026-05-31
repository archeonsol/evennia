# C1 + C2: boundary work finalization

Status: todo

## Goal

Ship the last two items in
[`engine-boundary-migration.md`](../docs/engine-boundary-migration.md)
and close out the boundary-work program:

- **C1**: follow / escort / shadow commands move upstream as default
  commands.
- **C2**: scene / IC broadcast helpers move upstream, plus a new
  `room_ic_viewers` typeclass hook.

These are independent items and can ship in either order. Group them
under one prompt because both are small upstreaming-fork-commands
work with the same shape: identify fork code, generalize, land
upstream, delete the fork shim.

## Background

Target items C1 and C2 in
[`engine-boundary-migration.md`](../docs/engine-boundary-migration.md).
Read that file first; it's short.

Both items are downstream of architecture-doc substrate:

- C1 depends on **M1** (composable `move_to`) in
  [`engine-api-architecture.md`](../docs/engine-api-architecture.md).
- C2 depends on **R1** (display pipeline) in the architecture doc.

## Dependency check (read this before touching code)

Before starting either item, **check whether the dependency has
shipped**:

- **M1**: look for `evennia/objects/move.py` or similar Move builder.
  Check the architecture doc's M1 entry for status.
- **R1**: look for `evennia/render/` or RenderNode type. Check the
  architecture doc's R1 entry for status.

**If the dependency has shipped**: target the new substrate API
(`Move(...)` builder for C1, `RenderNode` consumers for C2). This is
the preferred path.

**If the dependency has not shipped**: stop and confirm with the user
before proceeding. Options are (a) wait for the substrate, (b) ship
against the legacy API now and migrate later (incurs follow-up work).
Don't pick (b) silently.

## Approach

This prompt asks you to **propose a design before implementing each
item**.

1. Read the boundary-migration doc and the architecture doc entries
   for M1 / R1.
2. Read [AGENTS.md](../../AGENTS.md).
3. Run the dependency check above. Confirm path with user if either
   dep is missing.
4. For each item, propose the design (see questions below).
5. **Stop and get user review on each design proposal.**
6. After approval, implement that item. Repeat for the other.

C1 and C2 can be reviewed and shipped independently; you don't need
to bundle them.

## C1 design questions

- **Command set**. Are these three separate commands (`follow`,
  `escort`, `shadow`) or one command with modes? Original fork
  structure should inform this; don't redesign for its own sake.
- **Follower bookkeeping**. Where does the "who is following whom"
  state live? Attribute on character? Per-room state? Engine-level
  registry?
- **Mover-side invariant**. The boundary doc states: mover is in
  destination before followers are scheduled. Honor this. No
  cross-room ordering needed.
- **Move integration**. Builder integration with M1. Follow chains
  fire how many `Move(...).execute()` calls? One per follower?
  Batched?
- **Veto behavior**. What happens if a follower's `at_pre_move`
  vetoes? The mover already moved.
- **Cycle detection**. A follows B, B follows A. Engine concern?
  Detect at command time?
- **Hooks**. New hooks needed (e.g. `at_follower_arrived`) or just
  reuse existing move hooks?
- **Lock surface**. Who can follow whom? Default locks?

## C2 design questions

- **`room_ic_viewers` hook signature**. What does it return?
  Per-viewer filtering? Returns a list of objects, a generator, or
  something filterable?
- **Helper API shape**. What do the broadcast helpers look like?
  `room.broadcast_ic(node, ...)`? `scene.broadcast(...)`?
- **RenderNode integration**. Helpers take pre-rendered nodes, or
  render per-viewer? The latter is the P2 (render before deliver)
  pattern; pick it unless there's a real reason not to.
- **Hook discipline**. `room_ic_viewers` should be registered via H1
  with full schema. Specify event/phase/actor/returns at design
  time.
- **Filtering**. How are non-IC observers (admins, OOC viewers)
  excluded? Lock-based? Tag-based?
- **Migration**. What does the fork's current scene broadcast look
  like? How much of it stays game-side vs becomes engine default?

## Scope boundary

- **In scope (C1)**: follow/escort/shadow commands, follower
  bookkeeping, integration with M1.
- **In scope (C2)**: `room_ic_viewers` typeclass hook, scene/IC
  broadcast helpers, RenderNode integration.
- **Out of scope**: changing the move semantics themselves (that's
  M1's job); changing render semantics (R1's job); reworking the
  command system; new permission concepts.

## Existing code to study

- Fork commands for follow/escort/shadow (ask the user where they
  live; probably under fork's `commands/` or `world/`).
- Fork's scene broadcast helpers (same; probably under
  `world/scenes.py` or similar).
- `evennia/commands/default/general.py` — existing default-command
  shape for reference.
- `evennia/objects/objects.py` — DefaultObject location semantics.
- `evennia/objects/rooms.py` (or wherever room typeclass lives) —
  for the `room_ic_viewers` hook placement.

## Done means

**Per item:**

- Implemented upstream with tests.
- Fork removal noted (the equivalent fork-side shim should be
  deletable; coordinate with the user on actually removing it).
- Architecture doc M1/R1 entries updated if your work surfaces
  changes to the substrate contract.
- All existing tests pass.
- PR description summarizes the design.

**For closing out the boundary-migration program** (after both C1 and
C2 ship):

The live boundary-migration doc has three nuggets of ongoing
reference value that outlast the work program:

1. The **framing test** ("if a hypothetical second consumer could
   not reasonably re-implement this from scratch, it belongs in the
   engine"). Referenced from `FUTURE-IDEAS.md`.
2. The **settled language-agnostic policy** (engine declines to ship
   defaults; opinion downstream).
3. The **reclassification note** (Phase C/D items became I1/L1/I2 in
   the architecture doc).

Do this cleanup as a final commit after C1+C2 ship:

- Move those three nuggets into
  [`engine-boundary-migration-archive.md`](../docs/engine-boundary-migration-archive.md)
  as a new top section ("Settled policies and framing") above the
  existing historical content. The archive becomes the canonical
  home for both boundary history and settled boundary policy.
- Delete the live
  [`engine-boundary-migration.md`](../docs/engine-boundary-migration.md).
- Update [`AGENTS.md`](../../AGENTS.md) Docs section: the line that
  references both the live doc and the archive should collapse to a
  single archive reference; description updates to cover "history +
  settled policies."
- Verify `FUTURE-IDEAS.md`'s reference to the framing test still
  resolves (update the link target if needed).

Do not skip this cleanup; leaving the live doc as a stub after the
program closes invites confusion.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Proceeding when M1 or R1 dependency hasn't shipped (see dependency
  check above).
- Adding new hooks beyond `room_ic_viewers` (scope creep).
- Bundling C1 and C2 into one PR (they're independent; separate PRs
  are easier to review).
- Skipping the doc cleanup at the end (folding nuggets into archive,
  deleting live doc, updating AGENTS.md). The plan is pre-approved;
  just execute it.
