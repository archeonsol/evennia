# Deferred audits

Status: parked (no signal yet)

## Goal

This is an **umbrella prompt**, not a single task. Each entry
below is an audit that's been named but doesn't have enough
signal to start. Pick one when there's appetite or when an
incident gives one of them new weight.

For each audit, the right shape is:

1. Read enough of the surface to estimate scope.
2. Decide: real finding (spin out as its own prompt under
   `.agents/prompts/`) or no signal (write a one-line note
   confirming so and remove from this list).
3. Don't run the audit *and* the fix in one go — surface
   first, fix second.

## Audits

### Two-process boundary leaks

Portal-side code reaching server-side, or Django ORM bleeding
into Portal. Spot grep was clean; needs focused read.

Targets: `evennia/server/portal/` (look for `django` imports
beyond settings, ORM access, references to server-side
typeclasses).

### Mock-only test classes in `evennia/commands/tests.py`

That file is 6500+ lines. Spot-check 5-10 test classes for
"test exercises only the mock, not real behavior." Relevant
to the TDD-first belief in
[`core-beliefs.md`](../docs/core-beliefs.md).

### Wider `except *: pass` audit

83 sites total; hotspots:

- `evennia_launcher.py`
- `utils/utils.py`
- `utils/idmapper/models.py`
- `cmd_access_cache.py`
- `comms/models.py`
- `evmenu.py`
- `cmdsethandler.py`
- `cmdhandler.py`
- `building.py`
- `attributes.py`
- `help/utils.py`

F4 already narrowed bare excepts in four sites under
`+underspire.18`. This is the wider sweep.

### `@property` with side effects

File-by-file read. Properties that mutate state on access (cache
fills, lazy initialization) are usually fine but can hide
expensive work. Look for properties that trigger DB writes,
network calls, or external service touches.

### Logging convention

`log_warn` vs `warning`; `print(` outside CLI tools. Weak
standalone audit; could be folded into a touched-file pass
rather than swept.

### Settings zombies

~89 candidate dead settings. False-positive-heavy: many
settings are read by downstream consumers we can't grep from
in-repo. Probably needs cross-reference against Underspire
itself before any deletion.

### Type hints follow-up

The Phase 1-4 cleanup added type hints to files it touched.
Audit which engine modules still have zero type hints; pick
the ones that would benefit most from them (likely the
boundary-facing APIs, not the internal helpers).

## When to convert one to a real prompt

Signals that an entry below is ready to escalate:

- An incident traceable to the audit's premise (e.g. a
  swallowed exception bit us).
- A refactor wants the audit done first.
- The grep-count for one of the named hotspots changes
  significantly.

Otherwise, leave it parked. Audits without signal generate
noise.

## Ask before

- Spending more than a session per audit without surfacing
  findings.
- Acting on findings without spinning them out as their own
  prompt (this file stays an umbrella).
