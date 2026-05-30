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

Shipped bundles, rejected items, deferred work, and plugin-future
inhabitants live in
[`engine-boundary-migration-archive.md`](engine-boundary-migration-archive.md).

## Separate issue, not bundled

**`at_sync` reload path bug.** On session re-attach, `at_sync` silently
re-attaches a puppet without firing `at_pre_puppet`, leaving the
cmdset stack empty. File standalone with a focused repro.

## Language-agnostic position (post-Bundle 2)

Engine stays language-agnostic by *declining to ship defaults*, not by
abstracting language as a strategy protocol. Empty-default hooks
engine-side, opinion downstream. Opinionated language content lives in
a future language plugin. Consequences: drop the `Conjugator` strategy
item; drop pose/emote upstreaming (Bundle 3 → plugin-future);
`get_numbered_name`'s English pluralization is no longer "not a
violation," it's plugin-future cleanup.

## Bundle 2.x: language-agnostic polish

Same template-or-hook pattern as Bundle 2. Ship as `.42.1` or fold
into Bundle 3's first commit.

- A. `get_display_exits` label — route `_("Exits")` through
  `get_content_group_label("exits", looker)`.
- B. `{self}` self-pronoun — `at_say` mapping hardcodes `_("You")`; add
  `get_self_pronoun(looker)` hook or `self_pronoun` class attr.
- C. List joiner — three `iter_to_str(..., endsep=_(", and"))` sites;
  class attr `list_endsep` is cheapest.

Audits before scoping (may become D/E/F):

- D. Hardcoded arrival/departure strings in `at_post_move` /
  `at_post_arrive` / `at_post_leave`.
- E. Channel echo template on `DefaultChannel`.
- F. `get_numbered_name` `pluralize` / `article_for` hooks (likely
  plugin-future).

## Bundle 3: `.43+` — permissions discipline

10. **Quell-aware permstring helper.** Documented "check permstring
    respecting quell" helper. Stopgap so the trap closes even if 11 slips.
11. **`check_permstring` scope resolver.** RFC. Spec covers: quelled
    accounts resolve at the puppet's effective level; account vs
    character scope per built-in perm; migration for overriders.

## Bundle 4: later — largest moves

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

## Sequencing summary

| Release | Items |
|---|---|
| `.40` | 1, 2, 3, 4 (Bundle 1, shipped) |
| `.41` | universal veto/transform rule (Bundle 1.5, shipped) |
| `.42` | 5, 6, 7 (Bundle 2, shipped) |
| `.42.1` | A, B, C (language-agnostic polish) |
| any | `at_sync` bug, filed separately |
| `.43+` | 10, 11 (Bundle 3, permissions discipline) |
| later | 12, 13, 14 (Bundle 4) |
| Policy | `bump_*_generation` decision |
| plugin | English language pack (former item 9, `get_numbered_name`) |
| 6.1+ | deferred items |

Hardest push from the downstream side: items 1, 2, 5, 11. These touch
the largest amount of fork code today and are the closest analogs to
the `display_name_cache` move.
