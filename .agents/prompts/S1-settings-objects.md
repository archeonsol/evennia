# S1: settings as typed objects

Status: todo

## Goal

Replace global magic constants (`MULTISESSION_MODE`, `IDLE_TIMEOUT`,
`DEFAULT_HOME`, etc.) with typed setting objects supporting
validation, introspection, and scoping (global / per-account /
per-puppet where applicable).

## Background

Target item S1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).

Current settings live in Django's settings module and are read as
plain attributes from runtime code (`settings.IDLE_TIMEOUT`). Not
validated. Not introspectable. Not overridable per-scope.

## Approach

1. Read the architecture doc S1 item and existing settings usage.
2. Read [AGENTS.md](../../AGENTS.md).
3. Propose a design covering the questions below.
4. **Stop and get user review on your design proposal.**
5. After approval, implement with tests.

## Design questions to resolve in your proposal

- Setting class shape. Types (Int, Str, Bool, Path, Choice, etc.),
  defaults, validators, scoping declarations?
- Read API. Keep `settings.FOO` syntax (via `__getattr__` magic)?
  New namespace (`engine.settings.FOO`)? Both during transition?
- Scoping. How is global vs per-account vs per-puppet expressed?
  Where do scoped values live (DB? in-memory? both)?
- Migration of Django settings module. Wrap on read? Convert all at
  once? Subset for launch + gradual?
- Validation timing. At startup? On every read? On change?
- Override mechanism. How does a game override a setting today, and
  how does that continue to work?
- Deprecation path. Plain-attribute access keeps working with
  warnings, then removed later?

## Scope boundary

- **In scope**: engine-side settings
  (`evennia/settings_default.py` and the runtime reads of them).
- **Out of scope**: game-side settings (stay as Django config for
  now); web/REST configuration; environment-variable handling.

## Existing code to study

- `evennia/settings_default.py` — current setting definitions.
- `django.conf.settings` integration — how Evennia reads them today.
- Sites that read settings deep in the runtime (`grep
  settings.MULTISESSION_MODE`, etc.) for migration surface.

## Done means

- Setting class hierarchy implemented with tests.
- At least one high-traffic setting converted as a worked example.
- Migration plan documented (in the prompt or as a separate doc).
- All existing tests pass; existing setting access still works.
- Architecture doc S1 entry updated.
- PR description summarizes design.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Removing or renaming existing settings.
- Changing Django settings integration in ways that break game
  configuration.
- Scope creep into game-side or web settings.
