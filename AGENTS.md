# AGENTS.md

Guidance for AI coding agents in this repository. Evennia is a Python (>= 3.12) framework for building text-based multiplayer online games. It is a library, not a game.

## Quick Reference

Use `uv run` to execute commands in the project venv. Prefer `uv` over `pip`.

```bash
uv pip install -e .                          # dev install
make format                                  # black + isort
make lint                                    # black --check
make cleanrot                                # check agent context for rot
uv run pytest .agents/tools/tests/ -v        # agent tooling tests (pytest)
```

**Running Evennia tests**: `make test` requires `evennia` on PATH. With `uv run`, init a test game dir first and run from inside it. See [Testing](.agents/docs/testing.md).

## Key Rules

- **TDD**: write tests first. Prefer no-DB tests > mocks > full DB-backed tests.
- **Don't manually format code.** Run `make format` after editing.
- **All code must have Google-style docstrings.** See [Code Style](.agents/docs/code-style.md).
- **After editing agent context files** (anything under `.agents/` or `AGENTS.md`), run `make cleanrot` and fix all warnings before reporting done.

## Skills

Vendor-agnostic skills live in `.agents/skills/`. Vendor directories (`.claude/skills/`, `.codex/skills/`) symlink there.

## Docs

- [Core Beliefs](.agents/docs/core-beliefs.md) — design principles that guide tradeoffs (toolkit not game, think in Python not SQL, hooks not patches, compose don't branch)
- [Architecture](.agents/docs/architecture.md) — two-process model, typeclass system, command system, subsystems, flat API, settings; [Command System Contracts](.agents/docs/command-system.md) for hook order, AccountCommand, signals, session-proxy, matching, trie parser
- [Testing](.agents/docs/testing.md) — running tests, test base classes, DB setup, CI matrix
- [Code Style](.agents/docs/code-style.md) — docstring conventions, command docstring format
- [Development Commands](.agents/docs/commands.md) — install, game lifecycle, test/format commands, PR conventions
- [Releases & Versioning](.agents/docs/releases.md) — when to cut a release, files to update, changelog + tag procedure
- [CI/CD](.agents/docs/ci.md) — GitHub Actions workflows, test matrix, database configs, Docker, secrets
- [GitHub Issues & PRs](.agents/docs/github.md) — listing, searching, and reviewing issues/PRs with `gh` CLI
- [Hygiene Backlog](.agents/docs/hygiene-backlog.md), [Future Ideas](FUTURE-IDEAS.md), [Engine Boundary Migration](.agents/docs/engine-boundary-migration.md) (history + disposed items in [archive](.agents/docs/engine-boundary-migration-archive.md)) — parked findings, deferred design directions, and the upstream PR plan for engine/game boundary moves; check before re-deriving
- [Engine API Architecture](.agents/docs/engine-api-architecture.md) — two-layer engine target, render/deliver pipeline, lock objects, hook registry, etc. Gated on Underspire launch as API-freeze deadline. Companion to the boundary migration doc.
- [Engine Long-Horizon Sketch](.agents/docs/engine-long-horizon.md) — speculative 2.0-scale items (typeclass decoupling, headless mode, package split). Sketch-level, not committed; exists so Level 2 decisions don't close off Level 3 paths.
