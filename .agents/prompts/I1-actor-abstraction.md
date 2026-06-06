# I1: actor/context abstraction

Status: substrate shipped (CM1 Phase 3a, `evennia/actions/actor.py`) — closeout pending.

## What already shipped (don't rebuild)

The keystone primitive this prompt specified landed inside CM1 as `Actor`
(`evennia/actions/actor.py`): a computed view over the (session, account,
character) triple with derived `identity`/`focus`/`effective`/`location`/
`state_objects`, built deterministically from cmdhandler's `callertype` via
`Actor.from_caller`. Post-`.71` it resolves focus/identity from the durable
`ControlBinding` focus stack when one is attached (legacy triple is the
bindingless fallback). The action engine routes every dispatch through it.

So the design questions below are mostly **answered by what shipped**: a thin
computed wrapper (not a replacement), constructed per dispatch from
`callertype`, following the binding's focus, exposing the trio + derived views.

## Remaining to close (a reconciliation, not new substrate)

1. **`AccountCommand` fate — DECIDED: reframe as a marker.** Keep the class as
   the "runs in account context" base for its consumers (CmdOOC/CmdIC, comms,
   contribs); `Actor` is the source of truth. The `account_command_caller` flag
   + `cmdhandler._normalize_account_command_caller` are legacy-path-only and
   **retire with the legacy cmdset dispatch path** — the finish line of
   [CM1-input-capture-migration](CM1-input-capture-migration.md). Whether the
   bare marker class itself survives is settled when account/comms commands port
   in the **builder/command-group migration**. Marked `DEPRECATED (CM1 I1
   closeout)` in `commands/command.py` (flag + `AccountCommand`) and
   `commands/cmdhandler.py`.
2. **`self.caller` migration — DECIDED: defer.** It dies with the legacy default
   command modules as they port to native actions (the builder/command-group
   migration); do not pre-migrate. Intent recorded in the `Actor` class docstring
   (`evennia/actions/actor.py`).
3. **Stale self-references — DONE.** `actor.py`'s "until I1 lands" framing
   updated; this prompt + the arch-doc I1 entry reconciled.
4. L1 / R1 / I2 consume the `Actor` only nominally (not yet built; separate
   items, each unblocked by this substrate).

Net: I1's substrate is shipped and documented; the two residual items are
tracked under existing work (legacy-dispatch removal; command-group port), so
nothing new starts under the I1 banner. The original full prompt below is
retained for the design rationale.

## Goal

Build the substrate primitive that unifies session, account, puppet,
and effective permissions into one object. Resolves `self.caller`
ambiguity, gives L1 (lock objects + permission algebra) a place for
its `actor` argument, gives R1 (display pipeline) a place for its
`viewer` argument, gives I2 (multi-puppet shape) a place to live.

P3 (one canonical answer) applied to the identity space.

## Background

Target item I1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).
Migrated there from the boundary-migration doc as substrate.

Current state: `self.caller` in a command can be Session, Account,
or Object depending on cmdset routing. `AccountCommand` exists to
disambiguate. The session-proxy machinery papers over the rest. No
single object answers "who is acting, on what, with what authority."

This is the **keystone substrate item**: L1, I2, and R1 all consume
it. Get the shape right; it'll be load-bearing for the rest of the
arc.

## Approach

This item has the most open design questions of anything in Level 2.
Expect the proposal to require multiple chat rounds before
implementation begins.

1. Read the architecture doc I1 item, plus L1 and R1 (which depend
   on it) and I2 (which is downstream).
2. Read the existing identity machinery: ServerSession, AccountDB,
   ObjectDB.puppet, the session-proxy, AccountCommand.
3. Read [AGENTS.md](../../AGENTS.md) and
   [`Typeclass-Hooks.md`](../../docs/source/Components/Typeclass-Hooks.md) (B1's output;
   the hook contracts your Actor will interact with).
4. **Write a design proposal.** This should be a real doc (1-2
   pages), not a paragraph. Cover the questions below in detail.
5. **Stop and get user review.** Expect multiple rounds.
6. After approval, implement.

## Design questions to resolve in your proposal

- **Wrapper or replacement?** Is Actor a thin wrapper around the
  (session, account, puppet) trio, or a new type that the trio's
  current usages migrate onto?
- **Construction.** Where and when is an Actor instantiated?
  Per-command invocation? Cached on session? Created lazily on first
  access?
- **Lifecycle.** When does an Actor become invalid? Does it follow
  puppet changes (re-puppeting updates the same Actor) or is it
  bound to a specific puppet (new puppet = new Actor)?
- **Identity primitives.** What attributes does Actor expose
  (session, account, puppet, perms, scopes)? Methods or just data +
  helpers?
- **Migration of `self.caller`.** Does caller become an Actor?
  Stay as the trio with Actor as a sibling? Get deprecated?
- **`AccountCommand`.** Retire (Actor knows whether it's "account
  context"), reframe as a routing hint, or keep as-is?
- **Multi-puppet readiness.** How does Actor handle an account with
  multiple simultaneous puppets (I2 territory)? One Actor per
  puppet, or one Actor per account-with-multiple-puppets?
- **Permission integration.** Does Actor know its scope
  (`Scope.Account` / `Scope.Character` / `Scope.Effective` from L1)
  intrinsically, or does the caller pass scope to each `check`
  call?
- **Game code migration.** How invasive for game code that uses
  `caller`? Migration path?
- **Cmdset interaction.** Does Actor change cmdset assembly or
  routing, or is it purely a substrate primitive that cmdset uses?

## Scope boundary

- **In scope**: the Actor primitive itself, integration with the
  existing identity trio, basic migration of `self.caller`
  consumers.
- **Out of scope**: L1 (lock objects), I2 (multi-puppet), R1
  (rendering) implementations. I1 unblocks those; doesn't do them.
- **Out of scope**: rewriting `AccountCommand` or cmdset routing
  unless your design specifically requires it (flag it).

## Existing code to study

- `evennia/server/serversession.py` — Session and session-proxy.
- `evennia/accounts/accounts.py` — Account and puppet management.
- `evennia/objects/objects.py` — DefaultObject puppet/unpuppet.
- `evennia/commands/command.py` — Command and AccountCommand.
- `evennia/commands/cmdhandler.py` — how `caller` is determined for
  a given invocation.
- `evennia/locks/lockfuncs.py` — current permission checks (consumed
  by L1 later).

## Done means

- Actor primitive implemented with tests covering the lifecycle
  decisions in your design.
- `self.caller` migration story documented; at least minimal
  integration so existing commands keep working.
- L1, I2, R1 have a clear `actor` / `viewer` argument target.
- All existing tests pass.
- Architecture doc I1 entry updated.
- PR description summarizes design decisions and links the design
  proposal.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Touching cmdset routing or `AccountCommand` substantially (likely
  scope creep).
- Implementing L1, I2, or R1 (those are separate items).
- Mass-migrating game-side `caller` usage (gradual after I1; not
  part of I1 launch).
- Changing puppet/unpuppet semantics beyond what Actor needs.
