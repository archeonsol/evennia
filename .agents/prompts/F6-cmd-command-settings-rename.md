# F6: settings prefix `CMD_*` vs `COMMAND_*`

Status: todo

## Goal

One straggler setting (`CMD_ACCESS_CACHE_ENABLED`) uses the
`CMD_*` prefix; eight others use `COMMAND_*`. Pick one:

- **Rename** `CMD_ACCESS_CACHE_ENABLED` → `COMMAND_ACCESS_CACHE_ENABLED`.
  Breaking change (single symbol, Underspire-only consumer at
  this point). Recommended.
- **Document** the `CMD_*` vs `COMMAND_*` convention in
  `code-style.md` and leave the straggler alone.

## Approach

Recommended path is rename. It's one symbol, the consumer is
known, and consistent naming is cheap forever.

1. `grep` for `CMD_ACCESS_CACHE_ENABLED` across the entire repo
   plus any downstream consumer the user names (likely just
   Underspire itself). Confirm the consumer set.
2. Rename setting in `evennia/settings_default.py` (or wherever
   it lives) and at the read site(s).
3. Update any downstream consumer the user surfaces.
4. Note in `CHANGELOG-FORK.md`: breaking, single symbol,
   trivial migration.

If user picks document-instead: add a short section to
[`code-style.md`](../docs/code-style.md) covering the convention
and the carve-out.

## Background

Hygiene finding; the inconsistency has been sitting since the
settings layer evolved. Single-symbol rename was deferred
because it touches a public setting.

## Scope boundary

- **In scope**: this one setting and any direct consumers.
- **Out of scope**: broader settings hygiene (see S1 for the
  long-term typed-settings work); other prefix inconsistencies
  if you find them while grepping (file separately).

## Existing code to study

- `evennia/settings_default.py` — settings layer.
- `evennia/commands/cmd_access_cache.py` — the consumer.
- [`code-style.md`](../docs/code-style.md) — destination if
  document path.

## Done means

**Rename path:**

- Setting renamed; consumers updated.
- `CHANGELOG-FORK.md` entry under breaking changes.
- Migration note (one-line: rename the setting in your
  `server.conf`).

**Document path:**

- Convention written into `code-style.md`.
- `CHANGELOG-FORK.md` documents the carve-out.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Choosing document-over-rename without surfacing the choice
  (rename is the recommended path; flag if you want to deviate).
- Touching unrelated settings.
