# R1 completion: the universal render/deliver pipeline

Status: **design; the keystone.** The seams (emote / recording / photo) proved the
model. This finishes it: **every** viewer-facing output path becomes sugar over
`render(obj, viewer) -> RenderNode -> deliver(node, viewer)`. It replaces the
inherited string-append delivery wholesale, an engine change that benefits the game
as a whole (robust per-viewer naming, perception, psychosis, recordings, photos, any
future consumer), independent of any client.

## The problem with inherited delivery

Output today is built *and* flattened in one step, per call site: `msg`, `at_say`,
`return_appearance`, `get_display_name`, scene broadcast each produce a finished
**string**, with names/perception/psychosis already baked in via scattered
regex/NLP. There is no "render to a structured tree for viewer X" step separate from
"send." That is why identity is lost at the boundary, why psychosis distortions
string-match and silently fail, why photos re-parse rendered text, and why every
subsystem re-implements per-viewer naming.

## The seam

Two functions, one contract:

```
render(obj, viewer, *, kind, **ctx)  -> RenderNode      # structured, viewer-invariant refs
deliver(node, viewer, session=None)  -> None            # resolve per viewer, flatten per protocol, send
```

- **`render`** builds a `RenderNode`: a tree of blocks and inline spans holding
  *references* (character, exit, item, speech), not resolved names. Viewer-shaped
  facts (recog, disguise) are *not* pre-applied.
- **`deliver`** runs the per-viewer resolver + passes (identity, perception,
  psychosis, language), then flattens to the target protocol (ANSI for telnet,
  structured payload for the shell, plain for AI/accessibility, stored tree for
  recordings/photos). This is the single point where names become text.

`RenderNode` stays structured **all the way to the delivery boundary**, never
flattened mid-pipeline. That is what makes one render feed many consumers.

## The node model (generalize the span work)

Today's emote spans (`TextSpan / CharRef / PronounRef / SpeechSpan` in
`evennia/narrative/render.py`) are the inline vocabulary. Generalize to a block
tree: **block level** `Line`, `Paragraph`, `Section` (room desc), `ListBlock`,
`SystemBlock`; **inline level** the existing spans plus `ExitRef`, `ItemRef`,
`SelfRef`. New ref kinds resolve through the same resolver/pass machinery, which
already exists (`perceivable`, psychosis distortion, language) and stays; `deliver`
just applies it uniformly instead of per-surface.

## The string API becomes sugar

Backward-compatible facades keep every existing call site working:

```
obj.msg(text)            -> deliver(Line(TextSpan(text)), viewer)      # or pass-through
obj.at_say(...)          -> deliver(render(speaker, viewer, kind="say"), ...)
room.return_appearance() -> flatten(render(room, viewer, kind="look"), viewer)  # returns str
get_display_name(viewer) -> flatten(resolve CharRef(obj) for viewer)            # returns str
```

Legacy string callers get a flattened string; structured consumers (shell, store)
get the node. No call site is forced to change; string-building overrides keep
working until their surface is migrated.

## Strangler order (each surface parity-gated, then legacy retired)

Reuse the discipline that worked for emotes: build the node producer, gate on a
byte-parity canary against the legacy string, flip to span-sourced, retire the
legacy path. Order: `say`/`whisper` → `return_appearance`/room look (the ~17-override
minefield, behind a compat facade) → `get_display_name` (identity base namer, stays
put) → remaining name-bearing surfaces → `msg` itself (never thin-able, retired
last). Which surfaces have flipped and what remains is tracked in
[r1-migration-status.md](r1-migration-status.md); retirement of the legacy string
paths is gated on prod soak.

## Guardrails (unchanged, load-bearing)

- **Additive + parity-gated.** No surface flips until its node render is
  byte-identical to legacy for the baseline viewer; modifier-active viewers
  (psychosis/perception) are span-authoritative.
- **Identity ≠ display.** Refs carry real ids to the boundary; passes are cosmetic;
  mechanics never see distorted output.
- **No big bang.** One surface at a time; if a slice needs touching N unrelated
  overrides, it is the wrong slice.
- **`EVENNIA_REF`** must ship the seam symbols to prod; guarded imports keep it
  dormant until pinned.

## What exists vs what's new

**Exists:** the node substrate (`render.py`), the resolver + pass pipeline,
`deliver_node` (emote), recordings/photos, the byte-parity harness. **New here:**
the general block/inline node model, `render`/`deliver` as the named core, the
string-API-as-sugar facades, and migrating `say` → room look → `get_display_name` →
the rest → `msg`.
