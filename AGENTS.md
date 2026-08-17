# AGENTS.md

Guidance for AI coding agents in this repository. Evennia is a Python (>= 3.12) framework for building text-based multiplayer online games. It is a library, not a game.

## Quick Reference

Use `uv run` to execute commands in the project venv. Prefer `uv` over `pip`.

```bash
uv pip install -e .                          # dev install
make format                                  # ruff format + import sort
make lint                                    # ruff format --check + import-sort check
make cleanrot                                # check agent context for rot
```

**Running tests**: `make test` requires `evennia` on PATH — with `uv run`, init a test game dir first and run from inside it. Agent tooling tests live in `.agents/tools/tests/` (pytest). See [Testing](.agents/docs/testing.md).

## Key Rules

- **TDD**: write tests first. Prefer no-DB tests > mocks > full DB-backed tests.
- **Don't manually format code.** Run `make format` after editing.
- **All code must have Google-style docstrings.** See [Code Style](.agents/docs/code-style.md).
- **After editing agent context files** (anything under `.agents/` or `AGENTS.md`), run `make cleanrot` and fix all warnings before reporting done.

## Skills

Vendor-agnostic skills live in `.agents/skills/`. Vendor directories (`.claude/skills/`, `.codex/skills/`) symlink there.

## Docs

- [Core Beliefs](.agents/docs/core-beliefs.md) — design principles that guide tradeoffs (toolkit not game, think in Python not SQL, hooks not patches, compose don't branch)
- [Architecture](.agents/docs/architecture.md) — two-process model, typeclass system, command system, subsystems, flat API, settings; [Command System Contracts](.agents/docs/command-system.md) for hook order, AccountCommand, signals, session-proxy, matching, trie parser; [Default Actions](docs/source/Components/Default-Actions.md) for shipped rule providers and dispatch context; [Typeclass Hooks Contracts](docs/source/Components/Typeclass-Hooks.md) + [reference tables](docs/source/Components/Typeclass-Hooks-Reference.md) for hook contracts; [Web IO Boundary](docs/source/Components/Web-IO-Boundary.md) for worker ownership, timeout semantics, DTO rules, and stock website services; [Web Mutation Bridge](docs/source/Components/Web-Mutation-Bridge.md) for bounded admin/auth writes, lifecycle provenance, audit ordering, and extension rules
- [Testing](.agents/docs/testing.md) — running tests, test base classes, DB setup, CI matrix
- [Code Style](.agents/docs/code-style.md) — docstring conventions, command docstring format
- [Development Commands](.agents/docs/commands.md) — install, game lifecycle, test/format commands, PR conventions
- [Releases & Versioning](.agents/docs/releases.md) — when to cut a release, files to update, changelog + tag procedure
- [CI/CD](.agents/docs/ci.md) — GitHub Actions workflows, test matrix, database configs, Docker, secrets
- [GitHub Issues & PRs](.agents/docs/github.md) — listing, searching, and reviewing issues/PRs with `gh` CLI
- [Future Ideas](FUTURE-IDEAS.md) — deferred design directions; check before re-deriving. The engine/game placement rule lives in [Core Beliefs](.agents/docs/core-beliefs.md). Hygiene + architecture findings live as one-prompt-per-finding under [`.agents/prompts/`](.agents/prompts/).
- [Engine Architecture](.agents/docs/engine-architecture/index.md) — the engine's decisions record (two-layer thesis, P1-P3 principles): [decisions](.agents/docs/engine-architecture/decisions.md) (shipped), [committed](.agents/docs/engine-architecture/committed.md) (R1/L1/W1 not yet built), [horizon](.agents/docs/engine-architecture/horizon.md) (speculative). Check before re-deriving architecture.
