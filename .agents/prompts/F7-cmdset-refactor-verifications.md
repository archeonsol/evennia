# F7: cmdset refactor open verifications

Status: todo

## Goal

Three verification items parked from the retired
`CMDSET_REFACTOR.md §8`. Each is small and independent.

**(a) Account-cmd access cache hit rate.** Confirm the cache is
working under the Phase 2 `AccountCommand` caller assignment,
and that `_invalidate_cmd_access_caches`
(`cmd_access_cache.py:136-143`) walks correctly to puppeted
characters when an account-level cmdset changes.

**(b) `arg_regex` deprecation candidates.** Now that token-
boundary matching is the default, audit how many engine commands
still need `arg_regex` as an escape hatch. If none, deprecate
the parameter.

**(c) EvMore "q" re-test.** Confirm the sys-cmd dedup fix in
`cmdset.py:530` plus prefix-strip removal didn't reopen the
multi-match that originally motivated the dedup.

## Approach

These are three independent investigations. Do them in whichever
order, and ship findings as they land — no need to bundle.

For each: investigate, confirm or refute, then either close the
item (write a short note in CHANGELOG-FORK.md or wherever
appropriate) or open a fix as its own follow-up.

**Item (b) is the only one likely to produce code changes** (if
no commands need `arg_regex`, deprecate it). Items (a) and (c)
are verifications; if they pass, the deliverable is a note
confirming so, not a patch.

## Scope boundary

- **In scope**: the three items above, each in its own
  investigation.
- **Out of scope**: cmdset rethink (that's CM1; see
  [`CM1-cmdset-rethink.md`](CM1-cmdset-rethink.md)). General
  cache audit (that's F8; see
  [`F8-cache-invalidation-contracts.md`](F8-cache-invalidation-contracts.md)).
  Don't merge into either.

## Existing code to study

- `evennia/commands/cmd_access_cache.py` — particularly
  `_invalidate_cmd_access_caches` at lines 136-143.
- `evennia/commands/cmdset.py:530` — sys-cmd dedup fix.
- `evennia/commands/default/` — search for `arg_regex` usage
  across engine default commands.
- `evennia/utils/evmore.py` — for item (c) re-test setup.
- `.agents/docs/command-system.md` — current cmdset contracts.

## Done means

**Per item:**

- Verified or fix shipped with tests.
- One-line note in `CHANGELOG-FORK.md` recording the
  verification result (even if no code change).
- If item (b) deprecates `arg_regex`: deprecation path
  documented, removal release named.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Bundling these into one PR (separate is easier to review).
- Letting scope leak toward CM1 (cmdset rethink).
- Removing `arg_regex` immediately rather than deprecating it
  (callers downstream may exist).
