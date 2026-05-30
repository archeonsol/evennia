# M1: composable move_to

Status: todo

## Goal

Replace monolithic `move_to(...)` with a builder:
`Move(actor, dest).skip_messages().with_reason("teleport").execute()`.
Old `move_to(...)` becomes thin sugar that delegates. All
engine-internal callers use the builder form. Kwargs-as-API removed.

## Background

Target item M1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).

Current `move_to(...)` does lock check, hook chain, location change,
contents update, message broadcast, exit traversal in one call.
Suppressing any one piece means digging through kwargs. Callers that
want "move but don't message anyone" or "move but don't fire
at_post_move" have to know which kwargs disable what.

## Approach

1. Read the architecture doc M1 item.
2. Read the move-event section of
   [`typeclass-hooks.md`](../docs/typeclass-hooks.md) (B1's output)
   for the hook contracts your builder will fire.
3. Read [AGENTS.md](../../AGENTS.md).
4. Propose a design covering the questions below.
5. **Stop and get user review on your design proposal.**
6. After approval, implement.

## Design questions to resolve in your proposal

- Builder API. Fluent chaining
  (`Move(actor, dest).skip_messages().execute()`), kwargs to
  constructor (`Move(actor, dest, skip_messages=True).execute()`),
  or both?
- Method set. `skip_messages`, `with_reason`, `skip_hooks` (which
  hooks; all or selectable?), `with_exit`, `force`,
  `silent_to_origin`, `silent_to_dest`, etc. What does the menu look
  like?
- Validation timing. Lock check at construction? At execute? Both?
- Hook firing. Hooks fire at construction, execute, both? Per the
  hook contract in B1.
- Error handling. Exceptions for failures? Result type (Q1-style)?
  Boolean returns? Tradeoffs?
- Backward compatibility. `move_to(...)` keeps working as sugar over
  `Move(...).execute()`? When does it get deprecated?
- Internal caller migration. How many sites use `move_to` in
  engine code? Migrate all in M1 or worked example + gradual?

## Scope boundary

- **In scope**: builder API, internal caller migration, backward
  compatibility with old `move_to` signature.
- **Out of scope**: changing move semantics (the order of hook
  firing, the contents-update logic, etc.); adding new hooks; lock
  system changes.

## Existing code to study

- `evennia/objects/objects.py` — `DefaultObject.move_to` and its
  hook chain.
- Move-event section in
  [`typeclass-hooks.md`](../docs/typeclass-hooks.md).
- Engine call sites for `move_to` (grep) — the migration surface.
- `evennia/commands/default/general.py` and others — game-side-ish
  callers in default commands.

## Done means

- Builder implemented with tests covering: clean move, suppressed
  messages, suppressed hooks, lock failure, exit traversal.
- All engine-internal callers migrated to builder.
- `move_to(...)` continues to work as sugar.
- All existing tests pass.
- Architecture doc M1 entry updated.
- PR description summarizes design.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Changing hook firing order or semantics (out of scope; coordinate
  with B1's contract).
- Removing `move_to(...)` (gradual deprecation post-M1).
- Adding new lock checks or move-related hooks.
