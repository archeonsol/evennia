# F13: contribs half-policy

Status: todo

## Goal

Resolve the limbo in `evennia/contrib/`. Current stance is "no new
additions" but the existing contribs are public, tested, and
shipping. That's not a stable position. Pick one of:

- **Survivors-and-why.** Keep current contribs, document why each
  one survives the lane test, commit to maintaining them.
- **Deprecation window.** Set a removal date, announce, delete on
  schedule.

## Approach

**Start with discussion, not edits.** This is a policy decision and
the design space is small but the consequences ripple (docs,
release notes, downstream forks consuming contribs, future hygiene
findings that cite the "no new additions" rule).

1. Read [`core-beliefs.md`](../docs/core-beliefs.md) — the engine/
   game lane test and the contrib line live there.
2. Read [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md) for the
   three-lane breakdown.
3. List current `evennia/contrib/` contents. For each, note: what
   it does, last touched, downstream consumers known to user
   (ask), whether it would pass the lane test if proposed today.
4. **Present both options with tradeoffs.** Get user decision
   before any deletion or doc work.

## Context the user has

- F1 shipped in `+underspire.16`: six contribs moved to downstream
  newmoo (cooldowns, name_generator, traits, components, buffs,
  rpsystem). So contrib has already shrunk; the current set is
  what's left after one cull.
- F3 (doc rot sweep) is gated on a newmoo PR that moves more
  contrib modules. Coordinate timing.

## Scope boundary

- **In scope**: policy decision + the doc/changelog work to
  announce it. Deprecation timeline if that path. Survivors-and-
  why writeup if that path.
- **Out of scope**: actually deleting contribs in this prompt (a
  deprecation path produces a *schedule*, not the deletions).
  Migrating downstream consumers.

## Existing code to study

- `evennia/contrib/` — current contents.
- `docs/source/Contribs/` (or wherever contrib docs live).
- Past release notes / changelog for the F1 cull as precedent.

## Done means

- Policy is written down in [`core-beliefs.md`](../docs/core-beliefs.md)
  (or wherever the lane test lives) replacing the current
  "legacy bucket: no new additions" sentence with the resolved
  stance.
- If deprecation path: schedule documented, release notes
  updated, downstream consumers notified.
- If survivors path: per-contrib lane-test note added.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Deleting any contrib in this prompt (it's a separate followup).
- Picking the path without surfacing both options to the user.
