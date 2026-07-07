# Azaban — the shell wire protocol — design

Status: **proposed.** **Azaban** is our own WebSocket wire protocol (subprotocol
token `azaban.v1`), purpose-built for the Svelte shell and carrying **structured
R1 RenderNodes** instead of pre-baked HTML. Named to shed the "Evennia" branding —
it is *our* protocol v1, not "v2 of Evennia's". It slots into
the existing `evennia/server/portal/wire_formats/` subprotocol seam and coexists
with the MUD-standard and telnet formats — nothing else has to change.

## Landscape (what already exists)

- **`wire_formats/` package** with RFC-6455 subprotocol negotiation and a clean
  `WireFormat` base (`decode_incoming` / `encode_text` / `encode_prompt` /
  `encode_default`). Formats today:
  - `v1.evennia.com` — JSON arrays `[cmd, args, kwargs]`; text is **HTML**
    (`parse_html`). Legacy; the un-baked-ANSI path R1 moves past. *(The current
    shell speaks this.)*
  - `json.mudstandards.org` — binary ANSI + `{proto,id,data}` JSON envelope,
    OOB as GMCP-in-JSON. For **third-party standard WS clients**.
  - `gmcp_standard`, `terminal` — telnet/Mudlet (raw ANSI + GMCP/MSDP).
- **R1** already has `RenderNode` + span serialization (`RenderNode.payload()`,
  `span_to_dict`), and `deliver_node` ships a `narrative` OOB to capable sessions.

## Why a *new* format (not reuse `json.mudstandards`)

The MUD-standard format is for **interop**: `data` is a stringified blob and OOB
is GMCP-wrapped — lossy for rich structured payloads. Our shell is **ours, both
ends** — we want typed, structured messages (RenderNodes, per-viewer resolved),
not GMCP strings. So the shell negotiates its own `azaban.v1`; standard
clients keep their formats; **telnet/Mudlet are unaffected**. The name is
engine-generic (Azaban is the platform brand, not game-specific) — Underspire is
just its first consumer.

## Envelope

TEXT frames, one JSON object per frame, a **typed discriminated union**:

```jsonc
{ "t": "<type>", "seq"?: <int>, "re"?: <int>, /* ...typed payload... */ }
```

- `t` — message type (discriminant).
- `seq` — monotonic id for request/response correlation (RPC).
- `re` — "in reply to" `seq`.

JSON text frames are the default (debuggable). **`permessage-deflate` WS
compression is enabled** at the transport layer (free size win on room look /
recordings). BINARY frames carry asset payloads, and msgpack-encoded envelopes if
that encoding is negotiated in `hello`.

## Message types

**Server → client**
- `hello` — `{ server, session, caps }`. Sent on open; announces server capabilities.
- `text` — `{ html, kind? }`. System/narrative text as HTML (`parse_html`). The
  log line for anything not yet delivered as a structured node.
- `prompt` — `{ html }`.
- `render` — `{ nodes: RenderNode[] }`. Structured R1 narrative (blocks + inline
  span refs, per-viewer resolved). The shell renders nodes — clickable char refs,
  structured room look — instead of un-baking HTML. Emitted only to sessions that
  announced `caps.rendersNodes`; otherwise `deliver` flattens to `text`.
- `patch` — `{ target, ops }`. A delta to the client's structured **scene model**
  (room, occupants, exits, self/vitals, HUD). Applied reactively; no re-sent text.
- `asset` — `{ id, mime, meta, url? }` (+ optional following BINARY frame). A media
  asset (photo, portrait, map tile, audio) referenced by `id` from nodes/scene.
- `oob` — `{ ns, event, data }`. Typed, namespaced events not covered above
  (music, notifications, channel meta).
- `res` — `{ re, ok, data | error }`. RPC response.

**Client → server**
- `hello` — `{ client, caps: { rendersNodes, images, patches, assets, encoding, ... } }`.
- `cmd` — `{ line }`. A command line.
- `req` — `{ seq, ns, action, data }`. RPC (autocomplete, history, channel ops,
  `ns:"asset"` fetch).
- `oob` — `{ ns, action, data }`. Fire-and-forget client actions.

## RenderNode on the wire (the point)

`deliver` builds **one** RenderNode per output. For an Azaban session it ships the
node (+ optional `html` fallback); for v1/telnet it flattens to HTML/ANSI exactly
as today. The shell renders the node natively — clickable character refs,
structured room look, perception/psychosis already resolved server-side — instead
of un-baking ANSI. This is the R1 payoff realised end to end. Reuses the existing
`RenderNode.payload()` / span serialization.

## Capability handshake

Both sides exchange `hello.caps`. This **replaces the `CLIENT_NARRATIVE` flag**:
the server sees `caps.rendersNodes` and ships `render`; `caps.patches`,
`caps.assets`, `caps.images`, `caps.encoding`, `caps.theme`, etc. tailor delivery.
Unknown caps are ignored (forward-compatible), so features roll out per-cap.

## Scene model + patch updates (the experiential leap)

Stop modelling the world as only a *stream of text lines*; model it as a live,
per-viewer **structured world model** plus a narrative log. The server keeps a
scene per viewer — room (name/desc blocks), occupants, exits, self/vitals, HUD —
and streams `patch` deltas as it changes; the shell holds the model and renders it
reactively (Svelte). The **log** (emotes/says/system) stays a `text`/`render`
stream; the **scene** (room panel, occupant list, HUD, scene strip) becomes synced
state that updates *without* spamming the log. Ops are minimal JSON-patch-like
(`set` / `del` / `add` on a path); the model shape is versioned in the handshake.
Telnet/Mudlet get the text stream only — the scene model is shell-only. This is
what makes the web client feel next-gen while the MUD stays a MUD.

## Asset channel

Rich media (photographs — already captured server-side — character portraits,
district maps, audio, the diegetic Matrix visuals) is referenced by **id** from
nodes/scene, never inlined. `asset` announces `{id, mime, meta}`; bytes arrive as a
following BINARY frame or a fetchable `url` (client picks via `caps.assets`).
Cache-keyed by id. Keeps the text/structured protocol lean while unlocking media.

## One source of truth for the schema

Protocol types live in three mirrored places kept in sync by **shared round-trip
test vectors** (fixtures both sides encode/decode): this doc, `protocol.ts` in the
shell, and `wire_formats/azaban.py` on the portal. No codegen initially;
revisit if it drifts.

## Coexistence & the telnet invariant

`azaban.v1` is purely additive on the subprotocol seam:
- telnet/Mudlet → `terminal` / `gmcp` (raw ANSI); standard WS → `json.mudstandards`;
  legacy web → `v1.evennia.com` until the shell replaces it.
- The `deliver` boundary flattens the one RenderNode per negotiated format —
  **telnet output stays byte-correct** (explicit invariant, parity-gated).

## Migration

1. `wire_formats/azaban.py` — encode `text`/`prompt`/`oob`/`res`, decode
   `cmd`/`req`/`oob`/`hello`; register + round-trip tests.
2. Shell: negotiate `azaban.v1` (fallback `v1.evennia.com`); add the `hello`
   handshake, a typed OOB router, and the RenderNode renderer.
3. Wire `deliver` to emit nodes for Azaban sessions (reuse `RenderNode.payload()`).
4. Port OOB surfaces to typed `oob` messages incrementally (prompt → channels →
   hud → …), each parity-checked.
5. Keep `v1.evennia.com` until the shell fully replaces the legacy client, then
   deprecate. `EVENNIA_REF` bump ships the new format to prod.

## Open decisions (proposed answers)

1. **Name:** `azaban.v1` — **resolved** (drops the Evennia branding; engine-level
   platform name, Underspire is first consumer).
2. **`node` + `html` fallback on every text msg?** → node always; `html` only when
   cheap. The shell can flatten nodes itself for copy/paste, so the fallback is
   for degradation/logging, not the primary path.
3. **RPC now or later?** → the envelope supports `seq`/`re`/`req`/`res` from day
   one; implement specific RPCs (autocomplete, history) per-feature later.
4. **Transport:** WebSocket now (Twisted-native); **WebTransport (HTTP/3)**
   reserved as a future *transport* option — the wire format is transport-agnostic
   so message semantics won't change. Not worth the HTTP/3 infra lift yet.
5. **Encoding:** JSON default; **msgpack** as a negotiated `caps.encoding` added
   only if profiling ever shows JSON parse cost. `permessage-deflate` is on now.

## Build phases

1. **Azaban core (in progress):** `hello` / `text` / `prompt` / `cmd` / typed
   `oob`; portal `AzabanFormat` + shell negotiation. `render` reserved (narrative
   still flows as `text` until `caps.rendersNodes` is wired to `deliver`).
2. **`render`:** wire `caps.rendersNodes` → `deliver` emits nodes; shell renders
   the span tree (clickable refs). Retires the `CLIENT_NARRATIVE` flag.
3. **Scene model + `patch`:** server scene state + deltas; shell reactive model
   (room panel / HUD / occupants).
4. **Asset channel:** `asset` + binary/URL media (photos, portraits, maps).
