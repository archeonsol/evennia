# H1: hook registry

Status: todo

## Goal

Make every public hook registered with a declared signature, return
contract, and call site. `engine.hooks.for_event("move")` lists what
fires. Decorator-driven; introspection generates docs.
Registry-not-decorated hooks rejected at startup.

Executable form of B1 (the typeclass hooks taxonomy doc). B1 wrote
the contract; H1 makes it enforceable.

## Background

Target item H1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).

Today hook signatures are inconsistent: some return values matter,
some don't; some take `**kwargs`, some don't; some fire on actor,
some on room, some on both. No registry, no discovery, no signature
contract. Game devs grep the engine to find hooks.

B1 shipped two doc artifacts: the prose contract
([`Typeclass-Hooks.md`](../../docs/source/Components/Typeclass-Hooks.md)) and the
reference tables
([`Typeclass-Hooks-Reference.md`](../../docs/source/Components/Typeclass-Hooks-Reference.md)).
H1 turns those into runtime data.

## Approach

1. Read the architecture doc H1 item.
2. Read both B1 outputs above. Pay attention to the misshapen hooks
   list in B1; these hooks should not be registered as-is. Either
   rewrite them as part of H1 or coordinate with the user on
   sequencing.
3. Read [AGENTS.md](../../AGENTS.md).
4. Propose a design covering the questions below.
5. **Stop and get user review on your design proposal.**
6. After approval, implement.

## Design questions to resolve in your proposal

- Registration API. Decorator at hook definition site
  (`@register_hook(event="move", returns="veto", ...)`), separate
  registration call, or both?
- Schema. What does a hook entry carry: name, signature, return
  contract (veto / content / side-effect / mixed), fires-on
  (typeclass type), event grouping, override discipline?
- Misshapen hooks. B1's misshapen list flags hooks that don't fit
  the taxonomy. Architecture doc says rewrite first; that's
  potentially scope creep into H1's surface. Where's the line: H1
  rewrites them, H1 rejects them at startup, or there's a separate
  pre-H1 cleanup item?
- Startup validation. Registry-completeness check: every hook the
  engine fires must be registered, or warning-only? At startup or
  on first fire?
- Doc generation. How does the registry produce user-facing docs?
  Auto-generated from registry data?
- Performance. Introspection cost at startup? Per-hook overhead at
  fire time?
- Backward compatibility. Hooks not yet registered: hard error at
  startup, warning, or grace period? Game-side hooks: registered or
  out of scope?

## Scope boundary

- **In scope**: registration mechanism, decorator API,
  introspection, doc generation, startup validation, registry
  population for all engine-side hooks classified as clean by B1.
- **Out of scope**: registering game-side hooks (that's game
  responsibility); deep changes to hook semantics themselves.
- **Edge case to resolve in proposal**: misshapen-hook handling.
  Could be in scope or could be a separate pre-H1 cleanup item.

## Existing code to study

- [`Typeclass-Hooks.md`](../../docs/source/Components/Typeclass-Hooks.md) — B1's
  taxonomy and contracts.
- [`Typeclass-Hooks-Reference.md`](../../docs/source/Components/Typeclass-Hooks-Reference.md)
  — B1's reference tables.
- `evennia/objects/objects.py`, `evennia/accounts/accounts.py`,
  `evennia/scripts/scripts.py`, `evennia/comms/comms.py` — hook
  definitions to register.
- `evennia/commands/command.py` — command hooks (already documented
  in `command-system.md`; coordinate registry schema).

## Done means

- Registration mechanism implemented with tests.
- All clean engine-side hooks (per B1's classification) registered.
- `engine.hooks.for_event(...)` and other introspection APIs work.
- Startup validation runs and produces useful errors.
- Doc generation produces something useful (even if rough).
- All existing tests pass.
- Architecture doc H1 entry updated.
- PR description summarizes design and links B1 outputs.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Rewriting misshapen hooks at scale (might be in scope, might be
  separate; resolve in proposal).
- Changing hook firing semantics in any way (out of scope for H1).
- Requiring game-side hooks to register (potentially breaks games).
