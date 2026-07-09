# R1 rationale: resolve per-viewer at *view* time

The arc-wide *why* behind R1, shared by [r1-first-slice.md](r1-first-slice.md) (the
shipped emote seam) and [r1-universal-pipeline.md](r1-universal-pipeline.md)
(finishing R1 across every surface). See [committed.md](committed.md) for the R1/W1
targets.

## North star: not emit time

The real reason to do R1 is not "web gets nicer emotes." It fixes a structural flaw
in the whole IC-perception system: **names are baked in at emit, then frozen.**
Every surface flattens per-viewer text immediately and throws the structure away (a
pose builds a per-viewer string; a recording captures a **single** frozen
`camera_text`; photos snapshot resolved text). So a recording made now, replayed
later by someone who has *since recognized* a character, still shows the old sdesc.
Frozen strings cannot re-resolve.

**The fix:** keep a **`CharacterRef`** (a *reference*: char id + role, not a name)
inside the structure all the way to delivery. A single `render(node, viewer)`
resolves refs to names via the sdesc/recog resolver as the *last* step. Then:

1. **Consistency (code).** Naming logic duplicated across every emote/appearance
   builder collapses into **one** ref resolver; subsystem naming drift disappears.
2. **Storable + replayable (the functional win).** Recordings, photos, broadcasts
   store the **node tree** (refs unresolved) and resolve per whoever views, *when*
   they view. Recog applies retroactively, for free. Impossible with frozen strings.
3. **Composable per-viewer transforms.** Language garbling, blindness/darkness,
   disguise, distance, drunk-slur, voice tags become ordered render passes over the
   tree (`resolve_names → apply_language → apply_perception → flatten`) instead of
   tangled string surgery inside one emote builder.

Richness strings cannot express: a photo can mark *appearance* frozen (clothing at
capture) while *name* stays live (recog at view). Per-span freeze-vs-live is a
property of the node.

## v0 is a wrapper, not the real thing

The shipped v0 `RenderNode` (the emote slice) still carries `body` = a pre-flattened
per-viewer string, with `refs` as a parallel list. It proves the pipeline and hands
the web client type + refs, but **names are still baked at emit.** The wins above
need the **span decomposition**: the emote builder stops resolving names inline and
emits `[TextSpan, CharacterRef, TextSpan, SpeechSpan, ...]`; resolution moves into
`render(node, viewer)`. `flatten(node, viewer)` walks spans, resolving
`CharacterRef` via the sdesc/recog resolver and `SpeechSpan` via the language
passes, producing today's exact string (parity); a rich client renders each span as
a real node (clickable target).

**Parity gotcha (found by the byte-parity harness):** the legacy emote builder
double-wraps a possessive name when the name wrapper's leading char is a *non-word*
char. Production names are ANSI-wrapped (`|c...|n`, word-char-adjacent) so the
re-match never fires; the span renderer is single-wrap and matches production.
Before flipping any live surface to spans, gate on a parity test using **real**
characters + real skin-tones/features (not stubs) to confirm the ANSI-adjacency
assumption for every naming path.

## The one rule for this whole arc

R1 blows up the codebase if done as a refactor: `msg`, `return_appearance`,
`get_display_name`, `at_say` are the most-called and most-*overridden* paths in
engine + game. Changing their contract breaks every override at once. So R1 is a
**strangler**, never a rewrite:

- New path is **additive**: `render() → RenderNode → deliver()`.
- The string API keeps returning strings, unchanged. Structured delivery is layered
  underneath/alongside, gated on client capability.
- Migrate **one surface at a time**, each shippable end-to-end.
- **Guardrail:** if a slice needs touching N existing overrides, it is the wrong
  slice. Back out and pick a smaller one.
