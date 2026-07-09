# Releases & Versioning

Fork releases use PEP 440 local version identifiers:
`6.0.0+underspire.<n>`, where `<n>` increments on each tagged release. The
`+local` suffix means `pip` installs cleanly and never collides with upstream.

## When to cut a release

Any commit on `underspire` that ships user-visible behavior change,
engine API change, settings change, or a fix worth a tag in the
changelog gets a release. Pure agent-context tweaks
(`.agents/`, `AGENTS.md`) and trivial typo fixes don't.

If a change ships without a version bump, the next release that does bump
must backfill a changelog entry covering the skipped commits (so the
changelog stays a complete record of what shipped at each version).

**Fix arcs:** When iterating on one feature (e.g. BGM parity), land fixes on
`underspire` without tagging every commit. Cut **one tag** when the arc is
done; merge intermediate changelog entries into that release. Delete superseded
tags if they never reached production or were replaced (see `underspire.155`,
which replaced `.150`–`.154`).

## Files to update on every release

All four must move together. The merge commit and tag are the source
of truth — out-of-sync files between them mean a release is broken.

| File | What to change |
|---|---|
| [`pyproject.toml`](../../pyproject.toml) | `version = "6.0.0+underspire.<n>"` |
| [`evennia/VERSION.txt`](../../evennia/VERSION.txt) | Just the version string, single line |
| [`uv.lock`](../../uv.lock) | Run `uv lock` to regenerate — don't hand-edit |
| [`CHANGELOG-FORK.md`](../../CHANGELOG-FORK.md) | New entry at top under `---` separator |

At runtime, this string plus the current git rev is exposed as the package's
`__version__` (see [`evennia/__init__.py`](../../evennia/__init__.py)).

## Changelog entry format

Newest at top. Header is `## 6.0.0+underspire.<n> — <short title>`.
Sections by area (e.g. `### Engine`, `### Migration`, `### Performance`,
`### Tests`). Always include:

- **What changed** — concrete file references and the user-visible
  behavior delta. Link with markdown so the changelog is browsable.
- **Migration notes** for any required downstream changes (setting
  rename/removal, API surface change, breaking renames). For engine
  refactors with a dedicated migration doc, reference it from the
  changelog rather than duplicating.
- **Perf numbers** if the change is performance-relevant (added cache,
  changed parser, etc.). Include both the synthetic baseline and the
  expected real-world impact.

Past entries in [`CHANGELOG-FORK.md`](../../CHANGELOG-FORK.md) are
worked examples; mirror their depth and section structure.

## Release procedure

1. Land your work on a feature branch (e.g. `phaseN-feature-name`)
   off the current `underspire` HEAD. Per-step commits during development.
2. Bump version + write changelog as the **last commit on the branch**,
   not as a separate post-merge commit. The bump commit message should
   summarise the release (one-line title + body referencing the changelog
   entry).
3. Verify all four files are aligned: `pyproject.toml`, `VERSION.txt`,
   `uv.lock`, `CHANGELOG-FORK.md`. The changelog title must match the
   new version exactly. `uv lock` regenerates `uv.lock`.
4. Run `make cleanrot` if any `.agents/` or `AGENTS.md` files were
   touched on the branch.
5. If the branch added, removed, or renamed any public module or contrib,
   regenerate the doc trees so they stay aligned (skipping this is how the
   large `underspire.NN` doc drift accumulated: 99 undocumented new modules
   and 12 stale contribs in one jump). Both generators walk the filesystem,
   so no import or gamedir is needed:
   - api tree: `cd docs && EVDIR=$(realpath ../evennia) uv run --with sphinx make _autodoc-index`
   - contrib index + narrative pages: `uv run python docs/pylib/contrib_readmes2docs.py`
   - `git rm` any orphaned `docs/source/Contribs/Contrib-*.md` left behind
     for removed contribs — the generator writes live pages but never
     prunes stale ones.
   - For every removed contrib page, grep the narrative docs for inbound links
     and fix them (pruning the page leaves the prose that pointed at it). Both
     styles: `../Contribs/Contrib-<Name>.md` and autodoc `(evennia.contrib.<path>)`.
     Keep historical changelogs as history (de-link, don't rewrite); drop or
     reword active howto/concept recommendations pointing at a now-absent contrib.
6. Run the relevant test suite from the test game dir (see
   [Testing](testing.md)) before merging. Don't tag a release that
   doesn't pass.
7. Merge with `git merge --no-ff` into `underspire` so the feature
   branch's structure is preserved in history (each phase reads as a
   coherent merge in `git log --first-parent underspire`).
8. Tag on the merge commit: `git tag -a underspire.<n> -m "<title>"`.
9. The push (branch + tag) is done from the user's shell, not by an
   agent. Auth typically fails in agent environments.

## Backfilling skipped versions

If `VERSION.txt` was bumped (or a commit landed that should have shipped
under a version) without a changelog entry, the next release that ships
must add the missing entry above its own. Title and body should describe
what the skipped commit(s) actually did, citing the original commit hash.
Order in `CHANGELOG-FORK.md` stays strictly newest-first by version
number, regardless of when the entry was written.

## Anti-patterns to avoid

- **Bumping only `VERSION.txt`** without `pyproject.toml`, `uv.lock`,
  or a changelog entry. This is how the `+underspire.12` orphan
  (the redis_attr_cache fix in `ec39b3028`) happened — it was
  backfilled later but it's better to never create the gap.
- **Editing `uv.lock` by hand.** Run `uv lock` so the lockfile derives
  from `pyproject.toml`.
- **Tagging before tests pass.** Tags are immutable in practice; a
  broken tag is awkward to retract once anyone has fetched it.
- **Squashing the feature branch on merge.** Use `--no-ff` so the
  per-step commits stay browseable.
- **Vague changelog entries** like "Misc fixes" or "Refactor cleanup".
  Each entry should let a downstream consumer decide in 30 seconds
  whether the release affects them.
