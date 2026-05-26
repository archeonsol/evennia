# Hygiene Backlog

Catalogued findings from prior hygiene passes not yet acted on. Future
hygiene passes start here. Move entries to **Shipped** when they land;
delete entirely if explicitly decided "won't fix" (cite reason inline).

Severity: red = bug/belief violation; yellow = clear smell; green = polish.
Status: `open` (action recommended), `audit` (needs read before acting).

---

## Open

**F2. `evennia/server/deprecations.py` is upstream cargo.** Yellow.
~150 LOC. Raises on settings deprecated pre-1.0 upstream (`CMDSET_DEFAULT`,
`INLINEFUNC_*`, `TIME_SEC_PER_MIN…`, etc.) that Underspire never set.
Shrink or delete. Version bump.

**F3. Doc rot in `docs/source/`.** Yellow. 31 hits for removed APIs
(`MuxCommand`, `MuxAccountCommand`, `CMD_IGNORE_PREFIXES`) across
`Components/Commands.md`, `Howtos/Howto-*.md`, `Setup/Settings-Default.md`,
`Evennia-API.md`. Sweep after contrib-extraction; most contrib doc rot
dies with the moved modules.

**F4. Bare `except:` in engine.** Yellow. Three sites need typed
exception: `evennia/web/website/views/help.py:267,273`,
`evennia/utils/utils.py:2991`. Wider audit deferred: 83 total
`except *: pass` hotspots in `evennia_launcher.py` (6), `utils/utils.py`
(4), `utils/idmapper/models.py` (4), `cmd_access_cache.py` (4),
`comms/models.py` (3), `evmenu.py` (3), `cmdsethandler.py` (3),
`cmdhandler.py` (3), `building.py` (3), `attributes.py` (3),
`help/utils.py` (3). Each typed site is case-by-case.

**F5. Past-due TODO markers.** Green. Five scheduled for upstream 1.0/5.0
(fork is on 6.0): `comms/comms.py:934`, `objects/objects.py:2848`,
`commands/default/building.py:4275`, `utils/evmenu.py:670` and `:1011`.
Delete the dormant paths or convert to issues.

**F6. Settings prefix split `CMD_*` vs `COMMAND_*`.** Green. One
straggler (`CMD_ACCESS_CACHE_ENABLED`) vs eight `COMMAND_*`. Either
rename (breaking, single symbol) or document convention in
`code-style.md`. Triage pending.

**F7. CMDSET_REFACTOR §8 open items.** Yellow. Three need verification:

- Account-cmd access cache hit-rate / invalidation walk under Phase 2
  caller assignment (`evennia/commands/cmd_access_cache.py:136-143`).
- `arg_regex` deprecation candidate count post-token-boundary matching.
- EvMore "q" re-test after Phase 3 prefix-strip removal.

Each becomes its own finding once confirmed actionable.

---

## Audit (no action recommended until read)

**F8. Cache coherence.** Yellow. Caches: `location_cmdset_cache`,
`cmd_access_cache`, `display_name_cache`, lock cache, write-behind
attrs, trie cache (Phase 4), redis attr cache (`+underspire.12`).
Audit questions:

- Cross-cache invalidation between `cmd_access_cache` (signal-driven
  via `+underspire.9`) and the lock cache.
- "What fills me / what invalidates me / staleness bound" docstring
  coverage per cache (uneven).
- TTL / size caps: hardcoded vs setting, and which should flip.

Read-pass required before any cache-touching deletion work.

**F9. Big modules.** Green. Largest engine modules (lines):
`building.py` 4622, `objects/objects.py` 3822, `utils/utils.py` 3156,
`prototypes/menus.py` 2713, `evennia_launcher.py` 2385,
`utils/evmenu.py` 2140, `commands/default/comms.py` 2126,
`accounts/accounts.py` 2098, `typeclasses/attributes.py` 2015. No
splits proposed; surface only if a specific seam is named.

---

## Deferred audits (no signal yet)

Named in scope but not surfaced as findings. Listed so they aren't
silently dropped:

- **Two-process boundary leaks** (portal-side reaching server-side or
  Django ORM). Spot grep found nothing; needs focused read.
- **Mock-only test classes** in `evennia/commands/tests.py` (6500+
  lines). Spot-check 5–10 random classes for the "test exercises only
  the mock" lens.
- **`@property` with side effects.** File-by-file read.
- **Redundant `caller.account` / `caller.character`** reads post-
  `AccountCommand` normalisation (`+underspire.4`). Tractable as grep.
- **Logging convention.** `logger.log_warn` vs `logger.warning`;
  `print(` in engine outside CLI tooling. Audit found only CLI-adjacent
  / migration sites; weak standalone finding.
- **Type hints follow-up** on files touched during Phase-1-to-4
  refactor (`cmdparser_trie.py` typed, surrounding files less so).
  Touched-files-only per scope.

---

## Shipped

Move entries here when they land. Cite release.

- *(empty as of `+underspire.15`)*
