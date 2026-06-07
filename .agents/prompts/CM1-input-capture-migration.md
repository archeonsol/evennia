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

- **EvEditor** ([`evennia/utils/eveditor.py`](../../evennia/utils/eveditor.py)):
  the line-editor cmdset plus `CmdSaveYesNo` (key=`CMD_NOMATCH`,
  alias=`CMD_NOINPUT`). Captures every editor line + the save y/n prompt. Plus
  `persistent=True` reload survival. **Design decided — see "EvEditor design" and
  "Persistent-capture rehydration seam" below.**
- **Custom `CMD_NOMATCH` / `CMD_NOINPUT` overrides** anywhere else in the engine
  or contrib that relied on cmdset capture (grep to enumerate). Note the engine
  already ships native nomatch handling
  ([`evennia/actions/default/nomatch.py`](../../evennia/actions/default/nomatch.py));
  a game layers its own as `@rule(NoMatchAction, ...)` providers.

Already done — do **not** redo:
- `.73`: `get_input`, `ask_yes_no`, `@interactive`.
- `.78`: **EvMore** ([`evennia/utils/evmore.py`](../../evennia/utils/evmore.py)).
  `CmdSetMore`/`CmdMore`/`CmdMoreExit` deleted; replaced by `EvMoreState`
  (modeled on `GetInputState`/`YesNoState`). Engine-routed test in
  [`evennia/utils/tests/test_evmore.py`](../../evennia/utils/tests/test_evmore.py).
  Behavior change: `quit` now quits the pager (legacy quirk paged it forward).

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
  the **focus body** resolved by `capture_holder(caller, session)` — *not* the
  raw caller (see the holder/session-scope section below).
- EvMenu itself (`EvMenuState`) is the prior, larger example of the same pattern.

EvEditor captures a *richer* keyset than a single line, but the shape is
identical (and EvMore's shipped `EvMoreState` is a worked example of exactly
this): a capturing `StateProvider` that interprets the line and either stays
active (more editor input) or exits (quit / save-done).

## Capture-state holder + session scope (decided `.78` — follow this for EvEditor)

Input-capture is logically scoped to the **session** the prompt was shown to,
even though a state physically lives on a body. The engine reads active states
from `actor.holder` = `actor.focus` (the puppeted character IC, the account
OOC). The pre-`.78` sites installed on the **raw caller**, which silently misses
when caller ≠ focus (e.g. `EvMore(account, ...)` / `get_input(account, ...)`
while a character is puppeted — installed on the account, but the next line's
actor reads `holder = character`). Multi-puppet (`MULTISESSION_MODE` 2) also
rules out attaching to the account: a pager opened by session S1 must not seize
S2's unrelated input on a different character.

The decided model (do **not** re-litigate; apply it to EvEditor):

1. **Install on the focus, resolved like dispatch does.**
   `state.capture_holder(caller, session)`
   ([`evennia/actions/state.py`](../../evennia/actions/state.py)) returns
   `Actor.from_caller(caller, session=session).holder` — the exact body the next
   line's actor will read. Use it for both the `exit_state` (anti-stack) and
   `enter_state`. Body-scoped; **no `state_objects` aggregation / no engine
   semantics change** (the rejected "aggregate at read" alternative would have
   needed symmetric read/write across the focus stack + a snapshot race fix —
   more surgery, less correct).
2. **Session-guard the capture `before` rule.**
   `menus.session_mismatch(state_session, actor)` returns True when the state
   carries a session and it isn't the dispatching `actor.session`; the rule
   `return PASS`es so the other session's line falls through to normal dispatch.
   A `None` state-session is session-agnostic (legacy behavior). This makes the
   capture truly session-scoped even when two sessions drive the *same* body
   (mode 3): only the asking session's line is seized.

Retrofitted across `get_input`, `ask_yes_no`, `@interactive`
(`InputCaptureState` now takes `session`, threaded from `actor.session` in
`engine._get_input_deferred`), and `EvMore` (`.78`). EvEditor must do the same:
`capture_holder` for install/exit + `session_mismatch` in its capture rule.
Covered by `test_evmore.py::test_other_session_input_is_not_captured` /
`test_same_session_input_is_captured`; add the equivalent for EvEditor.

## EvEditor design (decided; build to this)

EvEditor is a big module (~1200 lines) but the migration surface is small — the
buffer/undo/display/load-save logic is untouched. Two parts:

**Part A — capture substrate (EvMore-shaped + command resolution).**
- Delete `EvEditorCmdSet`, `CmdLineInput`, `CmdEditorGroup`-as-cmdset,
  `CmdSaveYesNo`, `SaveYesNoCmdSet`. Add an `EvEditorState(StateProvider)`:
  `capture_input` (REDIRECT to `MenuInputAction`, with the `session_mismatch`
  guard) + `deliver_input` that routes the line. Install via `capture_holder` in
  `EvEditor.__init__`, exit in `EvEditor.quit()`. **Do not** auto-exit per line —
  the editor stays active until quit.
- **Command resolution:** EvEditor has ~30 `:`-commands with `arg_regex`
  longest-match + a NOMATCH fallback (any non-`:` line → append to buffer). Do
  **not** rewrite the ~360-line `CmdEditorGroup.func`. Drive the existing command
  objects from the state: reuse `cmdparser.build_matches` against an
  `EvEditorCmdSet` *instance* (pure string matching, not cmdset dispatch) to get
  cmdstring/args, then call the command's `parse()`/`func()`. No match → the
  buffer-insert path.
- **The one coupling to reroute:** `CmdEditorGroup.func`'s `:q` branch does
  `caller.cmdset.add(SaveYesNoCmdSet)` (cmdset manipulation in a command body).
  Replace with a state sub-mode (a save-confirm flag the next `deliver_input`
  reads) or reuse the engine `ask_yes_no`/`YesNoState`. This is the only spot you
  can't reuse verbatim.

**Part B — `persistent=True` survival across reload (use the shared seam below).**
`persistent=True` is the stock default for the help/attr/`@py` editors, so
dropping reload survival is a visible regression — keep it. The rebuild
machinery already exists (`_load_editor` recreates the whole `EvEditor` from the
persisted `_eveditor_saved` / `_eveditor_buffer_temp` attributes; recreating it
re-installs the state for free). What's gone is the *trigger*: today it rides the
persistent-cmdset reimport + lazy `_load_editor` in `parse()`, all of which we
delete. Re-point it through the rehydration seam.

## Persistent-capture rehydration seam (shared with the EvMenu migration)

**Two concurrent consumers, so build one seam, not two ad-hoc hooks.** EvMenu
(migrated in parallel by someone else) has the *identical* pattern: `persistent=True`,
a `_menutree_saved` attribute, and a `_restore(caller)` function that rebuilds
the menu — same dead trigger once its cmdset is deleted. Without a shared seam,
both migrations would each bolt a subsystem-specific block onto the base
`at_post_load` (merge collision + duplicated layering smell). Agree this contract
with the EvMenu owner before building.

States are non-persistent by design (`state.py`), and there is no generic
"reinstall persisted StateProviders on reload" path. Add a small **declarative,
attribute-keyed** one (attribute-keyed so the hook is cheap and imports the util
lazily *only* when an object actually has a suspended capture — a plain
register-on-import registry silently no-ops if the util module wasn't imported
before the reload):

```python
# evennia/actions/state.py (always loaded)
_CAPTURE_REHYDRATORS = [
    ("_eveditor_saved", "evennia.utils.eveditor.rehydrate"),
    ("_menutree_saved", "evennia.utils.evmenu.rehydrate"),
]
def rehydrate_captures(holder):
    """Reinstall any persisted capture state on `holder` after a reload.
    Idempotent + cheap: checks the marker attribute before importing."""
    from evennia.utils.utils import class_from_module
    for attr, path in _CAPTURE_REHYDRATORS:
        if holder.attributes.has(attr):
            try:
                class_from_module(path)(holder)
            except Exception:
                from evennia.utils import logger
                logger.log_trace()
```

- `at_post_load` (base typeclass — `typeclasses/models.py` covers objects; mirror
  in `accounts/accounts.py`, which overrides it) calls `rehydrate_captures(self)`
  alongside the existing schema-migration call. It "fires on every cache load …
  after every server reload" and must stay idempotent — so each `rehydrate` must
  no-op when the capture is already live (guard on `holder.ndb._eveditor` /
  `ndb._evmenu`).
- Each util exposes a thin idempotent `rehydrate(holder)` wrapping its existing
  restore (`_load_editor` / `_restore`).
- The table names consumers as **data**, not behavior; the two migrations both
  append their one row.

Reload test (no existing coverage — current `test_eveditor.py` is all
func-direct): start a `persistent=True` editor, simulate reload (drop `ndb`, call
`at_post_load`/`rehydrate_captures`), then dispatch a line through the engine and
assert it is still captured into the buffer.

## Approach (design-first, per this folder's convention)

1. The `StateProvider` design is settled (`EvMoreState` shipped; `EvEditorState`
   specified in "EvEditor design" above). Build to it; only re-open if you hit a
   contradiction.
2. For each subsystem, **engine-routed failing test first**, then migrate, then
   delete the dead cmdset, then confirm the new test passes and the old
   func-direct tests still pass. For `persistent=True`, add the reload test too.
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
  imports them (EvEditor still does today — that's why it comes first; EvMore no
  longer does as of `.78`).

## Scope boundary

- **In scope:** EvEditor, the cross-cutting holder-consistency decision above,
  and any remaining cmdset `CMD_NOMATCH` / `CMD_NOINPUT` capture; the `.73` nits
  above; planning the legacy-path removal. (EvMore done in `.78`.)
- **Out of scope:** migrating the unported **command groups** (`@dig`/`@tunnel`,
  `@set`/`@name`, batch processor) to native actions — that is a separate track
  (the `TestBuilding` / `TestBatchProcess` reds). Also out: the standalone
  plural-alias bug (`TestBuilding::test_name_clears_plural`,
  `box.aliases.get(category=box.plural_category)` returns `None`).

## Done means

- EvEditor (and any other cmdset-capture site) captures input via an engine
  `StateProvider`, **proven by an engine-routed test** that drives input through
  `cmdhandler` (not just the capture `func`). (EvMore done in `.78`.)
- EvEditor installs/exits via `capture_holder` and session-guards its capture
  rule with `session_mismatch` (the holder + session-scope model decided in
  `.78`; already applied to `get_input`/`ask_yes_no`/`@interactive`/`EvMore`).
- `persistent=True` editors survive a reload via the shared
  `state.rehydrate_captures` seam wired into `at_post_load`, **proven by a reload
  test** (not just func-direct). Seam contract agreed with the EvMenu owner so
  both subsystems append to one `_CAPTURE_REHYDRATORS` table.
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
