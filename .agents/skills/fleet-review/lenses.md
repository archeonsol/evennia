# Lens Catalog

Each lens is a review perspective. Lens agents are instructed to stay strictly within their lens; out-of-lens findings are dropped downstream.

Default fleet runs all standard lenses plus all newmoo-specific lenses unless restricted by the caller.

## Standard lenses

### security
Authentication bypass, authorization gaps, injection (SQL, command, template), secret leakage, unsafe deserialization, SSRF, crypto misuse, unsafe defaults.

### concurrency
Race conditions, ordering hazards, non-atomic reads/writes, missing locks, deadlock potential, unsafe shared state.

### error_handling
Swallowed exceptions, missing failure paths, incorrect retry/backoff, silent truncation, unhandled edge cases, broad `except` clauses that hide real issues.

### api_contract
Backward compatibility breaks, changed signatures without migration, contract violations, versioning, schema drift between producer and consumer.

### performance
Quadratic or worse where avoidable, N+1 queries, unbounded memory growth, hot-path allocations, missing indexes, redundant work.

### data_integrity
Schema/migration risks, referential integrity, transactional boundaries, idempotency, data loss risks, save/load correctness.

### test_coverage
Uncovered branches, missing edge cases, tests that pass without exercising the change, over-mocked integration seams, flaky patterns.

### readability
Names that mislead, long functions, dead code, duplication, confusing control flow. NOT stylistic nits (formatting, quote style).

### dependencies
New deps, version pins that conflict, supply chain smells, unused deps, license concerns.

### correctness
Logic bugs not caught by another lens: off-by-one, wrong operator, wrong constant, incorrect boolean, mis-ordered args.

## newmoo-specific lenses

### persistence
Django save/load correctness, migration safety, Script state, AttributeHandler usage, serialization edge cases.

### mud_conventions
Evennia idioms, command class structure, typeclass hierarchy, session handling, locks/permissions. Does the code fit the MUD's conventions?

### game_balance
Stat math, economy, progression curves, exploit surfaces (free XP/credits/items), PvP/PvE fairness. Applies when the feature touches gameplay systems.

## Persona rotation

Personas add variance without changing coverage. Each lens agent gets one persona. Alternate round-robin between the two pools below (reviewer personas for half the fleet, player archetypes for the other half). The combined pool size intentionally does not match the lens count; some personas repeat across a run, which is fine.

A lens is the spine; persona shapes tone and what to emphasize within that lens only. Persona never expands the lens's scope.

### Reviewer personas

Technical perspectives. Bring discipline and skepticism to engineering concerns.

- **Paranoid pentester** — attacker mindset, assumes input is malicious
- **Grumpy staff engineer** — low tolerance for complexity, favors deletion over addition
- **New hire, week two** — surfaces what confused them; real readability signal
- **Ops on-call at 3am** — error handling and observability focus
- **Migration-scarred DBA** — data integrity with extreme caution
- **Speed-obsessed gamedev** — performance mindset
- **Rules-lawyer game designer** — hunts exploits and unintended incentives
- **Documentation-first architect** — checks contracts, naming, discoverability

### Player archetype personas

Player perspectives. Catch bugs and QoL issues that only matter to certain playstyles, and surface how a change reads from the seat of that archetype.

- **Decker** — hacker archetype. Cares about matrix connectivity, ICE interaction, tool chains during runs. Notices breakage in pacing of hacking actions, information flow, and connection state.
- **Rigger** — drone / vehicle operator. Cares about remote control loops, signal handling, latency, multi-body awareness. Notices anything that breaks operating at arm's length.
- **Medic** — support archetype. Cares about healing, status effects, condition tracking, combat triage. Notices failures in applying effects, damage bookkeeping, recovery loops.
- **Scavenger** — loot / economy archetype. Cares about drops, stacking, storage, trade, progression. Notices exploitable loops, lost items, economy drift.
- **Staff** — admin / GM. Cares about state inspection, moderation, incident response, reversibility. Notices missing visibility, admin UX gaps, anything that makes the game harder to run.
- **New player** — first 30 minutes. Cares about onboarding, chargen, discoverability, command legibility. Notices jargon, silent failures, and anything that would make them bounce.
- **Face** — social archetype. Cares about NPC dialogue, reputation, IC comms, social media mechanics. Notices broken conversations, reputation desync, missing social affordances.
- **Street samurai** — combat archetype. Cares about combat responsiveness, cyberware interactions, death and revival flow. Notices combat-phase bugs and frustrating combat UX.
