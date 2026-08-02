# Azaban: runtime contract

The **design** of the protocol — envelope, message types, capability handshake,
scene model, build phases — lives in
[`webclient-protocol.md`](webclient-protocol.md). This is the operational half:
the properties the transport has to hold at runtime, each of which is wrong under
the obvious implementation. Split out because a design record and a set of
invariants-with-teeth age differently.

Code: `evennia/server/portal/webclient.py` (transport),
`evennia/server/portal/wire_formats/azaban.py` (codec),
`evennia/web/webclient/client/src/lib/evennia.svelte.ts` (client).

## The handshake

The client opens with `hello` carrying `caps` and a `resume` block. The server
replies with its own `hello` — `{protocol, resumed}` — **after** any replayed
frames and stamped last, so its `s` is above everything it replayed.

The client **assigns** its resume cursor from that `s` rather than taking a
maximum. This is the whole point of the reply: when the server could not resume
the session it restarts `out_seq` at zero, so a cursor that only ever climbs
would sit permanently above anything the new connection sends, and replay would
silently never fire again for that browser. A monotonic cursor here is a bug that
looks like working code.

## Resume

Outgoing frames carry a monotonic `s` and are buffered per connection. An unclean
close stashes the buffer under the client's token for `RESUME_GRACE_SECONDS`; the
client presents token + cursor in `hello` and gets back what it missed. State
events are idempotent, so the parallel fresh-login pushes are safe.

Three properties, each wrong by default:

- **The token is per *tab***, held in `sessionStorage`. In `localStorage` every
  tab of a browser presents the same token: they overwrite each other's stash on
  close, and a reconnecting tab replays another tab's frames into its own log.
  A reload keeps the tab, which is exactly the lifetime resume covers.
- **A stash only replays to the uid that produced it.** The key is chosen by the
  client, so it is bound to the authenticated uid at stash time and verified on
  claim — otherwise holding someone's token is enough to be handed the tail of
  their session.
- **The stash count is capped** (`RESUME_STASH_MAX`), not only time-limited.
  Each entry holds up to `RESUME_BUFFER_MAX` frames for the grace window, so an
  open/close loop with fresh tokens is a memory-growth lever.

## Frame encoding

A format with `supports_resume` returns the envelope **dict** from its `encode_*`
methods rather than encoded bytes. The transport must stamp `s` onto every frame
regardless, so handing over the object lets it serialize exactly once instead of
parsing back what the codec just dumped — three JSON passes per frame on the
batching path. Returning bytes stays valid; they are parsed back.

## Batching

A client declaring `caps.batching` gets a burst coalesced into one
`{"t": "batch", "frames": [...]}` flushed at the end of the reactor iteration,
capped at `BATCH_MAX_FRAMES`. The batch carries a single `s`; its members carry
none. Anything still queued must be flushed before the stash is taken on close,
or a reconnect replays a hole.

## Inbound limits

Structural caps are enforced before anything is routed: `_MAX_FRAME_BYTES` on the
raw frame, then depth/string/item bounds on the decoded shape. `req`/`oob`
actions are deny-by-default against `settings.AZABAN_PUBLIC_ACTIONS`, with an
always-denied set that no allowlist can re-open.

Rate limiting is **not** Azaban's own: every decoded frame goes through
`PortalSessionHandler.data_in`, which counts it against the shared
`MAX_COMMAND_RATE` budget. That budget is generous and shared, so an individually
expensive endpoint (one that rebuilds and pushes a full scene, say) wants its own
per-action interval on top, at the handler rather than here.

## The OOB event catalog

Events register in `evennia/server/protocol/` (engine) or a game's
`PROTOCOL_EVENT_MODULES`, and `python -m evennia.server.protocol.gen_ts` emits
`client/src/lib/oob-events.ts` from the merged registry. The shell routes on
`OobEvent`-typed literals, so an unregistered or renamed event fails the client
build instead of silently matching nothing at runtime.

Only a run with a game's settings sees the merged catalog, so the staleness check
for the generated file belongs in the game's test suite, not the engine's.
