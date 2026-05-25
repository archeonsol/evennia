# Cmdset Refactor — Downstream Migration Guide

Step-by-step notes for game-side updates as each phase of
[CMDSET_REFACTOR.md](CMDSET_REFACTOR.md) lands. Sections fill in as phases
merge.

For each phase: **Required** = must do or things break. **Auto** = engine
handles it but logs a warning. **Optional cleanup** = deletions you can do
once the new API is in.

---

## Phase 0 — CmdSet hygiene

**Required:** none. Phase 0 is purely additive.

**Auto:** none.

**Optional cleanup:**

- Replace any custom `safe_remove(cmdset, cmd)` helper with
  `cmdset.remove(cmd)` (now idempotent by default; returns `bool`).
- Replace `replace_command(cmdset, OldCmd, NewCmd())` helpers with
  `cmdset.replace(OldCmd, NewCmd())`.
- `cmdset.has(cmd_or_key)` is available for clarity over `cmd in cmdset`
  when checking by key string.

**Behavior notes:**

- `CmdSet.remove(missing_key)` no longer crashes when the key starts with
  `__` (was a latent `AttributeError`). Now returns `False`.
- `CmdSet.remove(cmd, strict=True)` is opt-in for the old raise-on-missing
  behavior.

**Side fix shipped with Phase 0:** `evennia.game_template.typeclasses.objects`
no longer registers a duplicate `Object` Django model. The
template-customizable `ObjectParent` mixin moved to
`evennia/game_template/typeclasses/object_parent.py`. Existing user game dirs
are unaffected — their `typeclasses/objects.py` keeps its own inline
`ObjectParent`. Tests against a `.test_game_dir/` adjacent to the engine
source now run without the `Conflicting 'object' models` error.

---

## Phase 1 — Engine hooks (kill monkey-patches)

_Pending. Will document signal subscription patterns and the session-proxy
contract here when the phase lands._

---

## Phase 2 — Account/Character split + hook reorder

_Pending. Anticipated migration:_

- **Auto (planned):** subclasses that define `at_pre_cmd` without
  `at_pre_parse` will have the engine alias the method onto `at_pre_parse`
  at class-creation time and emit a `DeprecationWarning` citing the file.
  Game keeps working as-is.
- **Required (planned):** to use the new post-parse `at_pre_cmd` semantic,
  rename your hook explicitly and adjust callsites.
- **Optional cleanup (planned):** delete game-side `AccountCommand`
  normalization (`_normalize_account_caller`, etc.) once the engine
  guarantees `self.caller`/`self.character` shape.

---

## Phase 3 — Token-boundary matching + drop prefix-strip

_Pending. Anticipated migration:_

- **Auto (planned):** `CMD_IGNORE_PREFIXES` honored for one release with a
  startup `DeprecationWarning` listing affected commands.
- **Required (planned):** cmdsets that worked around the `@cmd`/`cmd` alias
  collision (e.g. `safe_remove(CmdOpen)` + `CmdAtOpen()`) drop the
  workaround. Engine default builder commands are keyed `@open`/`@dig`/etc.
  directly.

---

## Phase 4 — Trie parser into engine

_Pending. Anticipated migration:_

- **Auto (planned):** `COMMAND_PARSER` default flips to the trie parser.
  Opt-out via setting to `evennia.commands.cmdparser_linear.cmdparser` if
  needed.
- **Optional cleanup (planned):** delete any game-side trie parser copy.
