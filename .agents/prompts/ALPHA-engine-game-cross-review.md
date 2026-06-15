# ALPHA: engine ↔ game cross-review (bidirectional)

Status: stub (do after the known alpha cleanups land)

## Context

The 2026-06-06 alpha audit was deliberately **engine-repo-only** (read-only,
repo-locked). It could not see two things that need both repos open:

1. **Engine code never used by the game** — surface area the engine carries that
   its single consumer doesn't touch. Candidates for removal, deferral, or
   "keep but document why it's speculative."
2. **Game code that should be promoted to the engine** — logic living in the game
   that is actually engine-shaped (agnostic, load-bearing for any game on this
   fork) and belongs upstream.

This is the bidirectional pass that closes the loop. It is a **stub**: no
findings are pre-listed because they require reading the game (`../newmoo`)
against the engine, which the prior audit did not do.

## Why it runs late

Do this **after** the known, named alpha items are resolved (the rest of the
[alpha-promotion audit](README.md) list), so the cross-review measures a settled
engine surface rather than chasing code that's already slated to move or die. Two
promote-to-engine cases are already broken out and should NOT be re-litigated
here: login (shipped in `.95`; prompt removed) and
[jobs/event-bus](ALPHA-jobs-eventbus-boundary.md).

## Goal

Produce a two-list report: (a) engine surfaces the game never consumes, each with
a keep/defer/cut recommendation; (b) game logic that should be promoted to the
engine, each with the agnostic-win rationale and a rough migration sketch. Spin
the actionable survivors out as their own prompts.

## Approach (cross-repo, read-only first)

1. Read both repos. Build the consumption map: for engine public surfaces (flat
   API, hooks, handlers, default actions/rules), find whether the game touches
   them. For game subsystems, judge whether they are game policy or disguised
   engine machinery.
2. Apply the engine-vs-game split principle (see
   [`core-beliefs.md`](../docs/core-beliefs.md) / the user's standing rule: the
   engine hosts only agnostic wins; game-shaped optimizations stay in the game).
   "Unused by this one game" is a signal, not a verdict — some engine surface is
   legitimately there for *other* future games on the fork.
3. Surface first, fix second: report the two lists and get direction before
   moving any code. Removals and promotions each become their own prompt.

## Scope boundary

- **In scope:** the bidirectional consumption/placement review across engine +
  game, and the resulting prompt stubs.
- **Out of scope:** login and jobs/event-bus (own tracks); contrib (slated for
  removal); anything already on the alpha-audit list.

## Done means

A reviewed two-list report (engine-unused / game-to-promote) with per-item
recommendations, and follow-up prompts written for the survivors the user
approves.
