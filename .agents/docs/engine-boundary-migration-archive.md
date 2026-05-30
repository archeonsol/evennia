# Engine/game boundary migration — archive

Disposed items pulled out of [`engine-boundary-migration.md`](engine-boundary-migration.md)
so the active plan stays in budget. Shipped history, rejected items,
deferred items, plugin-future inhabitants.

## Shipped bundles

Pointers only. [`CHANGELOG-FORK.md`](../../CHANGELOG-FORK.md) entries
are the source of truth.

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

## Rejected as not actually violations

`_content_types` taxonomy (entangled); `MULTISESSION_MODE` matrix as a
removal target (reframed as a half-built version of item 13);
`CmdHome` and other default cmdset commands that aren't language-shaped
(defaults are allowed to be opinionated, but language opinion belongs
in the plugin layer).

## Deferred

`PuppetPolicy` strategy replacing `MULTISESSION_MODE` (6.1 RFC,
subsumes mode-branching in Account/Session); channel subscription
mixin (wait until `.37` batching beds in); heap scheduler as engine
utility (ship as utility module if at all); observability helpers
(open question whether engine should expose any metrics surface);
engine cache invalidation coordinator (too tightly coupled, needs
generic abstraction first); cmdset audit dev tool (low priority).

## Plugin-future

Blocked on a plugin mechanism. First inhabitants of an English language
pack: pre-Bundle-2 `at_say` / `at_whisper` templates;
`Characters:` / `You see:` / `Exits:` labels; pose/emote/looc machinery
(former Item 9); English pluralization + articles (former
`get_numbered_name`); Oxford-comma joiner. No `Conjugator` protocol
needed; language packs ship language as code.
