# ENGINE: i18n policy for the action-engine layer

Status: todo (discussion-first; decide before any wrapping/unwrapping edits)

Surfaced during the `6.0.0+underspire.95` login review: the new action-engine
layer emits user-facing strings raw, but not uniformly, so there is no stated
policy on whether the engine is translatable.

## The state of play

- The action-engine layer (`evennia/actions/`, ~30 non-test files) is de facto
  English-only: sibling modules that emit player text
  (`default/general.py`, `default/movement.py`, `default/loginstart.py`,
  `default/unloggedin.py`) all use plain unwrapped literals
  (`caller.msg("Usage: home")`, `char.msg(f"There is no exit {direction}.")`).
- Only **two** files in the layer use `gettext`: `dispatch.py` and
  `default/nomatch.py`. So the inconsistency runs the other way from upstream
  Evennia: the engine is mostly unwrapped with two outliers that wrap.
- The legacy `commands/default/unloggedin.py` this layer replaced used zero
  `_()` either, so no translation coverage was lost in the rewrite.
- Upstream Evennia historically ships locale catalogs and wraps core strings, so
  an English-only engine is a deliberate departure from that contract, not an
  oversight to paper over.

## The decision

Pick one and record it, then make the layer consistent with it:

- **(a) Translatable engine.** Adopt `from django.utils.translation import
  gettext as _` as the layer convention and wrap every player-facing literal
  across the ~30 files; confirm the catalog/extraction story still works for the
  new layer. Cost: real, mechanical, and ongoing (every new engine string must
  be wrapped).
- **(b) English-only engine.** State that the action engine is not localized;
  then either unwrap the two `gettext` outliers for consistency or leave them
  with a one-line note. Cost: drops upstream's translatability contract for
  engine-emitted text.

The split that matters: which engine strings are genuinely player-facing
(connect screen, command usage, error feedback) vs. developer/diagnostic
(dispatch traces, nomatch internals). (b) is cheap if most are diagnostic; (a)
is worth it only if a downstream game actually serves non-English engine output.

## Scope

- **In scope:** the policy call, recording it in the engine-architecture docs,
  and the one-time consistency pass that follows.
- **Out of scope:** translating the game layer or contribs.

## Done means

A recorded decision (engine-architecture decisions record) and the
`evennia/actions/` layer uniformly consistent with it: no file half-wraps.
