# CM1: migrate cmdset-based input-capture to engine StateProviders

Status: todo

CM1 follow-on. Companion to [`CM1-action-system-roadmap.md`](CM1-action-system-roadmap.md)
(Phase 1–8 shipped) and the `6.0.0+underspire.73` release. Read the `.73`
`CHANGELOG-FORK.md` entry (the "Deprecation — legacy cmdset dispatch + cmdset
input-capture" section) before starting.

## Context

`.73` made the CM1 action engine the **sole player-input dispatch path**:
`cmdhandler` always bridges to `try_action_dispatch`, which always handles the
line. The legacy cmdset merge/match block in `cmdhandler` is now reachable only
by `cmdobj=` injection, on a one-version deprecation clock.

`.73` also migrated the first two input-capture utilities off cmdsets and onto
engine-native `StateProvider`s: `evmenu.get_input` and `evmenu.ask_yes_no` now
install `GetInputState` / `YesNoState` (in
[`evennia/actions/menus.py`](../../evennia/actions/menus.py)) instead of
`InputCmdSet` / `YesNoQuestionCmdSet`. `utils.interactive` (`@interactive`) is
built on `get_input`, so it was fixed transitively.

**What remains:** the other subsystems that still capture input via a cmdset of
`CMD_NOMATCH` / `CMD_NOINPUT` catch-all commands. The engine bridge never merges
those cmdsets, so the captured line goes to the engine (NoMatch/NoInput) and the
capture command never runs. These are **silently broken under the engine** today.

## The verification trap (read this first)

The reason this wasn't caught: **the existing tests for these subsystems call
the capture command's `func` directly, not through `cmdhandler`.** A green test
proves the `func` works in isolation; it does **not** prove the line still
reaches that `func` once the engine bridge owns dispatch. Green ≠ working here.

So: for every subsystem, **write an engine-routed test first** — drive input
through `cmdhandler` / `try_action_dispatch` (or a real session round-trip) and
assert the capture actually fires. That test should fail against `main` before
you migrate, and pass after. Do not trust the existing func-direct tests.

## Goal

Migrate every remaining cmdset-based input-capture subsystem to an engine
`StateProvider`, proven by engine-routed tests, and delete the dead capture
cmdsets. This clears the blockers for removing the legacy cmdset dispatch path
entirely (the next step after this).

## Subsystems to migrate

Confirmed cmdset-capture sites (grep `CMD_NOMATCH` / `CMD_NOINPUT` /
`cmdset.add` to find any others, including contrib):

- **EvMore** ([`evennia/utils/evmore.py`](../../evennia/utils/evmore.py)):
  `CmdSetMore` (`CmdMore` key=`CMD_NOINPUT`, `CmdMoreExit` key=`CMD_NOMATCH`),
  added at `self._caller.cmdset.add(CmdSetMore)`. Captures paging keys
  (next/back/quit/jump). Paging is everywhere (`return_appearance`, big lists),
  so this is the highest-impact one.
- **EvEditor** ([`evennia/utils/eveditor.py`](../../evennia/utils/eveditor.py)):
  the line-editor cmdset plus `CmdSaveYesNo` (key=`CMD_NOMATCH`,
  alias=`CMD_NOINPUT`). Captures every editor line + the save y/n prompt.
- **Custom `CMD_NOMATCH` / `CMD_NOINPUT` overrides** anywhere else in the engine
  or contrib that relied on cmdset capture (grep to enumerate). Note the engine
  already ships native nomatch handling
  ([`evennia/actions/default/nomatch.py`](../../evennia/actions/default/nomatch.py));
  a game layers its own as `@rule(NoMatchAction, ...)` providers.

Already done in `.73` (do **not** redo): `get_input`, `ask_yes_no`,
`@interactive`.

## The worked example to model on

`.73`'s `get_input` / `ask_yes_no` migration is the template:

- The state lives in [`evennia/actions/menus.py`](../../evennia/actions/menus.py)
  as a `StateProvider` (`GetInputState` / `YesNoState`), modeled on
  `InputCaptureState` / `EvMenuState`. A high-priority `before` rule REDIRECTs
  any non-menu action to a `MenuInputAction`; a `carry_out` rule delivers the
  captured line and `actor.exit_state(...)` when done.
- The utility enters/exits via
  [`evennia/actions/state.py`](../../evennia/actions/state.py)'s
  `enter_state` / `exit_state` (call `exit_state` first to avoid stacking), on
  the **caller** (which resolves to its actor).
- EvMenu itself (`EvMenuState`) is the prior, larger example of the same pattern.

EvMore/EvEditor each capture a *richer* keyset than a single line, but the shape
is identical: a capturing `StateProvider` that interprets the line and either
stays active (more paging / more editor input) or exits (quit / save-done).

## Approach (design-first, per this folder's convention)

1. Propose the design before editing: one `StateProvider` per subsystem (e.g.
   `PagerState`, `LineEditorState`), what each captures, how it interprets keys,
   and its enter/exit lifecycle. Get review.
2. For each subsystem, **engine-routed failing test first**, then migrate, then
   delete the dead cmdset, then confirm the new test passes and the old
   func-direct tests still pass.
3. Keep the public API stable (`EvMore(...)`, `EvEditor(...)` call signatures and
   behavior) — only the capture substrate changes.

## Adjacent cleanups (small, while you're in `menus.py` / `evmenu.py`)

Surfaced reviewing `.73`; fix opportunistically:
- `YesNoState.deliver_input` re-`raise`s on callback error while
  `GetInputState.deliver_input` swallows (returns `CLAIM`). Pick one deliberately
  and align both.
- `evmenu.get_input`'s docstring **Notes** still describe the deleted mechanism
  (`caller.ndb._getinput`, "stacking InputCmdSets", "cleared on concluding") —
  update to the `GetInputState` reality.
- `YesNoState` lowercases `inp` twice (the `isinstance(inp, str)` re-lower is
  dead after `raw.lower()`).

## Then: remove the legacy cmdset dispatch path

Once no subsystem captures via cmdset, the legacy merge/match block in
`cmdhandler` is reachable only by `cmdobj=` injection. Closing that out is the
finish line but needs its own care (do **not** fold it into a subsystem PR):
- `cmdobj=` callers (login `connect`, contrib menu commands) run a specific
  Command directly and bypass the bridge by design (`.73`). Rehome or preserve
  that path explicitly before deleting the surrounding legacy block.
- Per the roadmap, the `CMD_NOINPUT` / `CMD_NOMATCH` / `CMD_LOGINSTART` /
  `CMD_CHANNEL` constants retire with the legacy path. Confirm nothing still
  imports them (EvMore/EvEditor do today — that's why they come first).

## Scope boundary

- **In scope:** EvMore, EvEditor, and any remaining cmdset `CMD_NOMATCH` /
  `CMD_NOINPUT` capture; the `.73` nits above; planning the legacy-path removal.
- **Out of scope:** migrating the unported **command groups** (`@dig`/`@tunnel`,
  `@set`/`@name`, batch processor) to native actions — that is a separate track
  (the `TestBuilding` / `TestBatchProcess` reds). Also out: the standalone
  plural-alias bug (`TestBuilding::test_name_clears_plural`,
  `box.aliases.get(category=box.plural_category)` returns `None`).

## Done means

- EvMore and EvEditor (and any other cmdset-capture site) capture input via an
  engine `StateProvider`, **proven by an engine-routed test** that drives input
  through `cmdhandler` (not just the capture `func`).
- The dead capture cmdsets (`CmdSetMore`, the EvEditor cmdset, `CmdSaveYesNo`,
  etc.) are deleted.
- The `.73` nits are resolved.
- A clear, separately-reviewable plan for removing the legacy cmdset dispatch
  path + retiring the `CMD_*` constants (executed only after `cmdobj=` is
  rehomed and review sign-off).

## Repo conventions

See [AGENTS.md](../../AGENTS.md). TDD (engine-routed failing test first). Run
tests with Django's runner from `.test_game_dir/`. Format only touched files
(`uv run black <file>` then `uv run isort --profile black <file>`, line-length
100). Release-worthy: bump `VERSION.txt` + `pyproject.toml` + `uv.lock` + add a
`CHANGELOG-FORK.md` entry when the user asks to commit.

## Ask before

- Removing the legacy cmdset dispatch path or the `CMD_*` constants (needs
  `cmdobj=` rehoming + sign-off).
- Changing any public `EvMore` / `EvEditor` API surface.
- Touching the separate command-group or plural-alias tracks (out of scope here).
