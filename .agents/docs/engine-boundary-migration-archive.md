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
- **Phase A — `+underspire.43`.** Four small items grouped in one
  release:
  - **A1 (language-agnostic polish).** Bundle 2's seam pattern applied
    to the three remaining hardcoded-English sites in `AppearanceMixin`:
    `get_display_exits` routes its `Exits:` prefix through the existing
    `get_content_group_label("exits", looker)` hook (default `""` —
    label drops engine-side); new `get_self_pronoun(looker)` method
    replaces the three hardcoded `_("You")` mappings in `at_say` (default
    still `_("You")`, but per-receiver looker context is now plumbed
    through); new `list_endsep` class attr replaces the three
    `iter_to_str(..., endsep=_(", and"))` call sites (class attr
    sufficient, no plausible viewer variation). Audit items (movement
    broadcasts, channel echo, `get_numbered_name` pluralization)
    explicitly deferred to opportunistic work-as-touched.
  - **A2 (flat API hygiene).** `evennia/__init__.py` triple-declaration
    pattern (top-level `= None`, `global` in `_init`, import in `_init`)
    replaced with a `_LAZY_EXPORTS` registry plus PEP 562
    module-level `__getattr__`. Explicit `__all__` declares the public
    surface. `_init` retains only the dynamic container construction
    (`managers`, `default_cmds`, `syscmdkeys`) and portal-vs-server boot
    state. No behavioral change in the post-`_init` state; pre-`_init`
    access now triggers lazy load (and may surface `ImproperlyConfigured`
    if Django isn't set up) instead of returning the historical `None`.
  - **A3 (`bump_cmdset_generation` contract).** Docstring on the hook
    (in `evennia/commands/location_cmdset_cache.py`) now describes the
    invalidation contract: when callers must fire, what cache
    guarantees the bump provides, who the intended consumers are. Hook
    kept (zero engine callers outside its own cache, but the only known
    downstream consumer would otherwise have to monkey-patch the merge
    path). Test pins the docstring against silent rot.
  - **A4 (`at_sync` reload bug fix).** `ServerSession.at_sync` now
    fires `at_pre_puppet` / `at_post_puppet` with `reattach=True`
    on the puid re-attach path, so non-persistent puppet state
    (most visibly the merged cmdset stack) rebuilds after a server
    reload. Default `at_post_puppet` (in `DefaultObject` and
    `DefaultCharacter`) short-circuits on `reattach=True` to avoid
    re-echoing "You become X" / look output / room broadcasts on
    every reload. Fix is deliberately scoped to firing the hooks;
    Phase C (identity model) will reshape this territory.

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
