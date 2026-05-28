# Engine/game boundary migration

Plan for pushing fork-owned infrastructure upstream into Evennia core
and trimming engine code that belongs game-side. Items are ordered by
implementation sequence.

Framing test (see [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md)): if a
hypothetical second consumer could not reasonably re-implement this
from scratch, it belongs in the engine. Reference precedent:
`display_name_cache` moved game-side in `underspire.36`; the
`get_display_name` seam it sat on stayed engine-side. Bundle 1 items
are the closest analogs.

## Bundle 1: `.39` — additive seams + hook-naming sweep

Release note covers the new hooks AND the renames. Downstream is
lock-step so deprecation cycles are skipped; aliases are not added.

1. **`at_puppet_added(char)` / `at_puppet_removed(char)` on `DefaultAccount`.**
   Empty hooks called from the existing puppet/unpuppet path on
   first-attach / last-detach. Unblocks multi-puppet relays without
   monkey-patching.
2. **`get_extra_display_state(looker) -> ""` on the appearance mixin.**
   Called from `return_appearance` via the `{extra_state}` template
   key, appended to the name line. Stub returns `""`; overrides return
   content with a leading newline if they want it on its own line.
   Unlocks pose, AFK, mood, combat stance, status flags.
3. **`get_default_lockstring()` on `DefaultCharacter`.** Already landed
   in the fork via `LifecycleMixin.get_default_lockstring` and per-type
   overrides. The literal `lockstring` class attr on `DefaultCharacter`
   is now dead code; cleanup is a separate trivial PR.
4. **Movement hook rename + new `at_post_leave`.** Renames:
   `at_pre_object_leave` → `at_pre_leave`,
   `at_pre_object_receive` → `at_pre_arrive`,
   `at_object_receive` → `at_post_arrive`. Drops `at_object_leave` (its
   pre-move side-effect semantics now belong in `at_pre_leave`, which
   must return `True` to allow). Adds `at_post_leave` firing on the
   source after the location change. Ordering: `at_post_leave` and
   `at_post_arrive` unordered relative to each other; both fire before
   mover-side `at_post_move`. Downstream sweep required: rename every
   override of the old four hooks; for old `at_object_leave` bodies,
   either rename to `at_pre_leave` and append `return True`, or rename
   to `at_post_leave` if the side effects do not depend on the object
   still being in the room.
5. **`at_init` → `at_post_load` on every typeclass.** The historical
   name suggested object creation; it actually fires on idmapper cache
   load. Rename on `TypedObject`, `DefaultObject`, `DefaultAccount`,
   `DefaultBot`, `DefaultScript`, `DefaultChannel`, `DefaultExit`. All
   call sites updated. Downstream sweep required: rename every override
   of `at_init` to `at_post_load`.
6. **Add `at_pre_rename(oldname, newname)` to `TypedObject`.** Returns
   `True` by default; return `False` to veto. Fired before the rename
   commits, giving games a place for reserved-name checks, conflict
   resolution, audit logs, or normalization without overriding the
   `key` setter. Identity renames (`oldname == newname`) now no-op and
   skip both hooks.
7. **Traverse refactor.** Renames `at_traverse` (the implementation
   method) to `do_traverse` on `DefaultObject` and `DefaultExit`, since
   it performs the move rather than firing as a notification. Adds
   `at_pre_traverse(traversing_object, target_location)` with veto
   semantics on `DefaultObject`; `do_traverse` calls it first and
   routes to `at_failed_traverse` on `False`. `at_post_traverse` and
   `at_failed_traverse` are unchanged. Downstream sweep required:
   rename every override of `at_traverse` to `do_traverse`.

## Separate issue, not bundled

**`at_sync` reload path bug.** On session re-attach, `at_sync` silently
re-attaches a puppet without firing `at_pre_puppet`, leaving the
cmdset stack empty. File standalone with a focused repro.

## Bundle 2: `.40` — minor stock break, release-noted

5. **Empty `at_say` / `at_whisper` default templates.** Replace literal
   English templates in `objects/mixins/appearance.py` with `return ""`.
   Real games override wholesale.
6. **`get_content_group_label(group) -> ""` on the appearance mixin.**
   Replace hardcoded `"Characters"` / `"You see"` labels.
   `get_display_characters` / `get_display_things` skip empty groups.
7. **Cmdset merge cache warmup hook.** Pure performance utility, opt-in.
   Primes the cmdset merge cache on login/reload. Lands in
   `evennia/commands/` or as an opt-in startup helper.

## Bundle 3: `.41` — design-led

8. **`Conjugator` seam (Language strategy).** Prerequisite for 9. RFC.
   `Conjugator` protocol with `conjugate(word, person, tense)` and
   `EnglishConjugator` default; settings-selectable. Without this,
   moving pose/emote upstream imports English rules into core.
9. **Pose / emote / looc primitives.** Blocked on 8. Move `CmdPose` /
   `CmdEmote` / `CmdLooc` plus emote machinery (conjugation,
   target-aware "you" substitution) as a default cmdset overlay. Pairs
   with hook 2 for persistent pose display.
10. **Quell-aware permstring helper.** Documented "check permstring
    respecting quell" helper. Stopgap so the trap closes even if 11 slips.
11. **`check_permstring` scope resolver.** RFC. Spec covers: quelled
    accounts resolve at the puppet's effective level; account vs
    character scope per built-in perm; migration for overriders.

## Bundle 4: `.42+` — largest moves

12. **Follow / escort / shadow commands.** Blocked on 4. Move upstream
    as default commands. Mover-side invariant: mover is in destination
    before followers are scheduled. No cross-room ordering needed.
13. **Multi-puppet relay + slot primitives.** Blocked on 1. Session
    relay and P1/P2/P3 slot machinery. Death/incapacitation gates stay
    game-side behind try/import. Relay reads only the puppet markers
    set at slot assignment; survives a future `PuppetPolicy` cleanly.
14. **Scene / IC broadcast helpers.** Blocked on 2 and a
    `room_ic_viewers` typeclass hook. Batch the hook with Bundle 1 if
    possible.

## Policy call required (not a move)

**`bump_*_generation` hooks.** Zero engine callers outside contrib;
only known consumer is the fork. Keep as documented forward-looking
seam (any game with viewer-aware display names plus cached lookups
would want them) or remove as unconsumed. Recommend keep with an
invalidation-contract comment. Upstream call.

## Deferred

`PuppetPolicy` strategy replacing `MULTISESSION_MODE` (6.1 RFC,
subsumes mode-branching in Account/Session); channel subscription
mixin (wait until `.37` batching beds in); heap scheduler as engine
utility (ship as utility module if at all); observability helpers
(open question whether engine should expose any metrics surface);
engine cache invalidation coordinator (too tightly coupled, needs
generic abstraction first); cmdset audit dev tool (low priority).

## Rejected as not actually violations

`_content_types` taxonomy (entangled); `get_numbered_name` English
pluralization (real bias, no meaningful engine cost);
`MULTISESSION_MODE` matrix as a removal target (reframed as a
half-built version of item 13); `CmdPose` / `CmdHome` / default cmdset
opinions (defaults are allowed to be opinionated).

## Sequencing summary

| Release | Items |
|---|---|
| `.39` | 1, 2, 3, 4 |
| any | `at_sync` bug, filed separately |
| `.40` | 5, 6, 7 |
| `.41` | 8, then 9, 10, 11 |
| `.42+` | 12, 13, 14 |
| Policy | `bump_*_generation` decision |
| 6.1+ | deferred items |

Hardest push from the downstream side: items 1, 2, 5, 11. These touch
the largest amount of fork code today and are the closest analogs to
the `display_name_cache` move.
