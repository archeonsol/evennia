# F9: big modules audit

Status: audit (read before acting)

## Goal

Read the largest non-test engine files end to end and surface
**lane-3 (game) seams** that escaped into lane-1 (infra), or
lane-1 surfaces buried inside cohesive lane-3 implementation.

Largest non-test engine files by LOC (snapshot — recount when
starting):

- building 4622
- objects 3822
- utils 3156
- prototypes/menus 2713
- evennia_launcher 2385
- evmenu 2140
- default/comms 2126
- accounts 2098
- attributes 2015

**Suspect first: `default/comms`.**

## Background

Lane definitions live in
[`core-beliefs.md`](../docs/core-beliefs.md) ("Engine is a toolkit")
and [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md). The audit applies
the lane test ("could a hypothetical second consumer reasonably
re-implement this from scratch?") to large modules where
implementation creep typically hides.

## Approach

This is an **audit**, not a refactor. Read first, write findings,
get user agreement on which findings become work.

1. Recount LOC; the snapshot above is stale by definition.
2. Pick one module to start. `default/comms` is the strongest
   suspect.
3. Read end to end. Note: which functions/classes look lane-3
   (game-shaped, opinionated, ship-because-Underspire-wants-it)
   vs lane-1 (infra, would be needed by any consumer)?
4. Write findings as concrete items: file:line, lane judgment,
   recommendation (move, split, keep, document).
5. **Stop before any code change.** Present findings; user
   picks which to ship as follow-up prompts.

Don't read all nine modules in one pass. One per session is
plenty; the findings get more accurate when you've slept on the
prior one.

## Scope boundary

- **In scope**: reading and producing findings.
- **Out of scope**: any code change. Surfaced findings become
  separate prompts.

## Existing code to study

- The nine files listed above, in `evennia/`.
- [`core-beliefs.md`](../docs/core-beliefs.md) for the lane test
  (framing test + agnostic-by-opt-out) — the rule for what counts as
  which lane. (Prior worked boundary judgments are in git history,
  in the retired `engine-boundary-migration` docs.)

## Done means

**Per module audited:**

- Findings written up. Per-finding: file:line, lane judgment,
  one-paragraph rationale, recommendation.
- User has triaged findings into act / drop / defer.
- Acted-on findings become their own prompts in
  `.agents/prompts/`.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Making any code change during the audit pass.
- Reading multiple modules in one session (degrades signal).
- Producing findings without rationale (a recommendation
  without why is noise).
