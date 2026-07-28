# R1 completion: the universal render/deliver pipeline

Status: **engine substrate and documented game-surface migration complete.** The two
are tracked separately in [r1-migration-status.md](r1-migration-status.md) —
"the engine can do this" and "the game does this" are different claims, and
reading them as one hid real work.

The seams (emote / recording / photo) proved the model. Every viewer-facing output
path can become sugar over `plan(obj) -> resolve(plan, viewer) -> deliver(node,
viewer)`. This engine change serves per-viewer naming, perception, psychosis,
recordings, photos, and future consumers independently of client.

## The problem with inherited delivery

Inherited call sites build and flatten a finished string in one step, with
names/perception/psychosis baked in. Without a separate viewer-resolution step,
identity is lost, psychosis must string-match, photos re-parse prose, and every
subsystem reimplements naming.

## The seam

Three functions, two types:

```
plan_for(obj, *, kind, **ctx)   -> RenderPlan    # canonical, viewer-invariant refs
resolve(plan, viewer)           -> RenderNode    # references become this viewer's text
deliver(plan, viewer)           -> None          # resolve, transform, flatten per protocol, send
```

- **`RenderPlan`** is a deeply immutable, bounded tree whose inline spans hold
  references, not resolved names. It has no `body`, preventing viewer-resolved
  output from being stored as canonical structure.
- **`resolve`** runs the per-viewer resolver + passes (identity, perception,
  psychosis, language) and derives everything the viewer gets — the flattened
  `body`, the block tree, the opaque handles — from that one resolution.
- **`deliver`** publishes the canonical event once, runs the ordered universal transforms, and flattens to the target
  protocol (markup → ANSI for telnet, structured payload for the shell/GMCP,
  plain for AI/accessibility, `storage_payload()` for recordings/photos).

The plan stays structured **all the way to the delivery boundary**, never
flattened mid-pipeline. That is what makes one event feed many consumers.
`flatten_blocks` emits Evennia markup, never ANSI or HTML, so one flatten result
serves xterm256, no-colour and screenreader sessions alike.

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

When blocks exist, transforms map their leaves and the engine re-derives
`RenderNode.body`. Body-only changes are rejected, preventing telnet and
structured clients from receiving different realities.

## Shipped R1A-R1D contract

- `RenderPlan` and its spans are deeply immutable and bounded before resolution;
  `RenderNode` is immutable and versioned as `render.v1`, with bounded metadata,
  semantic blocks, correlation/node IDs, and separate trusted storage payloads.
- Rich-client references use expiring viewer-scoped handles. Raw database IDs are
  rejected from narrative and scene-patch wire payloads.
- `msg()` and `msg_contents()` normalize through the node core for every session.
  Object and account message hooks run once before the universal transforms, and
  protocol fan-out cannot re-run either. Text-only sessions retain byte-parity
  output. Literal emotes and room looks no longer bypass structured delivery.
- Every capable session receives nodes. Azaban normalizes remaining text to a text
  node, and the shell renders blocks and handles natively.
- A bounded semantic timeline plus read-only sinks provides replay, accessibility,
  recording, and camera integration seams. Canonical publication is idempotent by
  plan ID; relays deliver another perspective without publishing another event.

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

- **Additive + parity-gated.** A surface flips only at baseline byte parity;
  modifier-active viewers are span-authoritative.
- **Identity is not display.** Trusted invariant spans may retain real IDs inside
  the server; delivery issues opaque viewer handles. Mechanics never infer identity
  from distorted output and clients never receive database IDs.
- **No big bang.** Migrate one surface at a time.
- **`EVENNIA_REF`** must ship the seam symbols to prod; guarded imports keep it
  dormant until pinned.

## What exists vs what's new

**Exists:** the node substrate (`render.py`), the resolver + pass pipeline,
`deliver_node` (emote), recordings/photos, the byte-parity harness. **New here:**
the general block/inline node model, `render`/`deliver` as the named core, the
string-API-as-sugar facades, and migrating `say` → room look → `get_display_name` →
the rest → `msg`.
