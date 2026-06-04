# F19: tag-search flat-API facade is incomplete and lopsided

Status: todo

## Goal

The flat tag-search API (`evennia.search_tag` and siblings in
`evennia/utils/search.py`) is a thin facade over the manager method
`TypedObjectManager.get_by_tag()`. The facade is fine in principle but
has two concrete flaws worth resolving:

1. **Incomplete coverage.** Wrappers exist for only four model types
   (Object, Account, Script, Channel). Engine code that searches tags
   on `Msg` or `DbPrototype` has no wrapper and *must* reach into the
   manager directly. That isn't a boundary violation; it's a gap.
2. **Lopsided usage.** Of the four wrappers, three are effectively
   dead: `search_account_tag` (0 callers anywhere),
   `search_channel_tag` (0), `search_script_tag` (tests only). They
   exist for API symmetry but nothing uses them, including the engine.

Decide a coherent shape: either complete the facade (add the missing
wrappers) or trim it (drop the dead ones), rather than leaving four
wrappers where one is used and three are not.

## Evidence (as of this writing)

Game side is clean: newmoo has **0** direct manager calls; all 11 tag
searches go through `search_tag` / `search_object_by_tag`. The facade
does its job for game devs.

Engine side has 9 direct `get_by_tag` calls. Grouped:

- **No wrapper exists for the model** (legitimate manager use):
  - `evennia/contrib/game_systems/mail/mail.py:130,132` — `Msg.objects`
  - `evennia/prototypes/prototypes.py:629` — `DbPrototype.objects`
  - `evennia/prototypes/tests.py:484,494,509` — `DbPrototype`, tests
- **Wrapper exists, could have used it** (`ObjectDB` lookups):
  - `evennia/server/service.py:171`
  - `evennia/prototypes/prototypes.py:697`
  - `evennia/prototypes/spawner.py:666`

Wrapper usage counts (engine, excluding tests): `search_tag` 2,
`search_object_by_tag` 5, `search_account_tag` 0, `search_script_tag`
0 (tests only), `search_channel_tag` 0.

## Approach

**Start with discussion.** This is a small API-shape decision, but the
right answer depends on intent:

1. Confirm the counts above still hold (grep both repos:
   `.get_by_tag(`, `.get_by_permission(`, `.get_by_alias(`, and each
   `search_*_tag(` wrapper).
2. Decide the facade's purpose. Two coherent positions:
   - **Complete it.** The flat API should cover every searchable
     model. Add `search_msg_tag` and a prototype wrapper, migrate the
     three "could have used it" `ObjectDB` call sites to `search_tag`.
     Keep the dead wrappers since symmetry is now the point.
   - **Trim it.** The flat API exists for the common case only
     (`ObjectDB`). Drop the three dead wrappers; engine code uses
     managers directly for everything else, which is fine for internal
     code.
3. The naming inconsistency is a separate, smaller question: `search_*`
   (utils) vs `get_by_*` (managers), plus `search_tag_object`/`get_tag`
   which return Tag objects rather than tagged objects. Note it; don't
   bundle a rename into this finding unless the user wants it.
4. **Present recommendation and get a decision** before adding or
   removing any public API surface.

## Scope boundary

- **In scope**: the `search_*_tag` wrapper set and whether it matches
  its callers; migrating the three `ObjectDB` direct calls if "complete
  it" wins.
- **Out of scope**: `get_by_tag` itself (the implementation is fine and
  must stay exposed for managers). The broader flat-API audit. Any
  rename of `get_by_*` vs `search_*`. The Tag-object vs tagged-object
  naming muddle (`search_tag_object`/`get_tag`).

## Existing code to study

- `evennia/utils/search.py` — the wrappers (~lines 348-400).
- `evennia/typeclasses/managers.py` — `get_by_tag` (~196),
  `get_by_permission`/`get_by_alias` (~296-320), `get_tag` (~107).
- `evennia/__init__.py` (or the flat-API export point) — confirm which
  wrappers are actually re-exported as `evennia.search_*`.

## Done means

- Explicit decision (complete vs trim) recorded in the commit message.
- If trim: dead wrappers removed; flat-API export list updated;
  deprecation handled if any were ever public (changelog note).
- If complete: missing wrappers added with Google-style docstrings;
  three `ObjectDB` call sites migrated; symmetry documented.
- Either way: a one-line note in the engine-boundary doc that the tag
  facade was resolved, so it isn't re-derived.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Removing any wrapper that turns out to be re-exported as public
  `evennia.search_*` API without a deprecation path.
- Bundling the `get_by_*` vs `search_*` rename into this work.
- Adding wrappers for models nobody searches by tag yet (only add
  `Msg`/prototype wrappers if "complete it" is the chosen direction).
