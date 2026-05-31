# F20: prototype `exec` key

Status: todo

## Goal

`prototypes/spawner.py:874` runs arbitrary Python from the `exec`
key in a prototype dict. `building.py` gates this on Developer
permission. Decide:

- **Rip it out.** Spawn-time hooks belong on typeclasses
  (`at_object_creation`, `at_init`), not in prototype dicts as
  inline Python. Replace any in-tree consumers with hook
  overrides.
- **Document the gate as permanent.** The Developer gate is
  load-bearing; keep it, write that down where prototype
  authors will see it.

## Approach

**Start with discussion.** This isn't pure mechanical work because
the answer depends on how the fork actually uses `exec`-keyed
prototypes.

1. Read [`spawner.py:874`](../../evennia/prototypes/spawner.py)
   and the call site that runs the exec'd code.
2. `grep` for prototypes that use `exec:` keys in-tree and in
   any consumer the user points you at. If zero in-tree
   consumers and the user has none downstream, **rip-out is the
   right call**.
3. Read [`building.py`](../../evennia/commands/default/building.py)
   for the Developer gate; trace the path. Confirm the gate
   actually fires before exec runs.
4. **Present findings to user**: number of consumers, gate
   status, recommendation. Get decision before editing.

If rip-out: replacement guidance is hooks on the spawned
typeclass; `at_object_creation` runs once on first spawn,
`at_init` runs every load.

## Background

F5 surfaced this. F5 also corrected a stale comment in
`building.py` about the exec gate; the gate itself wasn't
touched then.

## Scope boundary

- **In scope**: the `exec` key in prototype dicts and its gate.
- **Out of scope**: broader prototype-system rework; spawn
  pipeline redesign; locking model changes.

## Existing code to study

- `evennia/prototypes/spawner.py:874` — the exec call site.
- `evennia/commands/default/building.py` — the Developer gate.
- `evennia/prototypes/prototypes.py` — how prototype dicts
  reach spawner.
- In-tree prototype examples for `exec:` usage (likely under
  contribs or game-template dirs).

## Done means

**If rip-out:**

- `exec` key handling removed from spawner.
- Any in-tree consumers migrated to typeclass hooks.
- Documented in release notes as breaking change for downstream
  prototype authors who used `exec:`.
- `building.py` gate removed if it was only for this.

**If document-the-gate:**

- Permanent-comment at spawner exec call explaining the gate
  contract.
- Prototype-author docs mention the Developer requirement.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Removing the gate or weakening it without explicit decision.
- Rip-out without migrating in-tree consumers first.
- Letting scope leak into general prototype redesign.
