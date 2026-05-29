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

## Shipped bundles

History compressed to pointers once a bundle has landed. The changelog
entries in [`CHANGELOG-FORK.md`](../../CHANGELOG-FORK.md) are the source
of truth for what each bundle actually contained.

- **Bundle 1 — `+underspire.40`.** Additive seams + hook-naming sweep:
  `at_puppet_added` / `at_puppet_removed`, `get_extra_display_state`,
  `at_pre_rename`, movement hook renames + `at_post_leave`,
  `at_init` → `at_post_load`, traverse refactor (`at_traverse` →
  `do_traverse` + new `at_pre_traverse`).
- **Bundle 1.5 — `+underspire.41`.** Universal veto rule and transform
  rule across every pre-hook. Adds `is_veto(result)` and
  `resolve_transform(result, original)` in `evennia/utils/utils.py`;
  makes `at_pre_puppet` veto-capable; documents `at_pre_unpuppet` /
  `at_pre_login` / `at_pre_parse` / `at_pre_cmd` as exceptions.
- **Bundle 2 — `+underspire.42`.** Three appearance/perf items:
  overridable `at_say` template hooks
  (`get_say_template_self`/`_location`/`_receivers` → `""`),
  `get_content_group_label(group, looker) → ""` driving the
  `Characters:` / `You see:` prefixes, cmdset merge cache warmup module
  at `evennia/commands/cmdset_merge_warmup.py` wired into
  `puppet_object` and the post-reload `at_post_portal_sync` hook.

## Separate issue, not bundled

**`at_sync` reload path bug.** On session re-attach, `at_sync` silently
re-attaches a puppet without firing `at_pre_puppet`, leaving the
cmdset stack empty. File standalone with a focused repro.

## Bundle 3: `.43+` — design-led

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
| `.40` | 1, 2, 3, 4 (Bundle 1) |
| `.41` | universal veto/transform rule (Bundle 1.5) |
| `.42` | 5, 6, 7 (Bundle 2, shipped) |
| any | `at_sync` bug, filed separately |
| `.43+` | 8, then 9, 10, 11 (Bundle 3) |
| later | 12, 13, 14 (Bundle 4) |
| Policy | `bump_*_generation` decision |
| 6.1+ | deferred items |

Hardest push from the downstream side: items 1, 2, 5, 11. These touch
the largest amount of fork code today and are the closest analogs to
the `display_name_cache` move.
