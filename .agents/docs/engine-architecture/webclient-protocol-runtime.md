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
- **Replay storage has count and byte caps.** Each connection retains at most
  400 frames and `WEBSOCKET_RESUME_BYTES` (default 32 MiB). Detached stashes
  additionally share `WEBSOCKET_RESUME_STASH_BYTES` (default 128 MiB) and a
  512-stash cap. Per-connection pressure evicts oldest whole frames; global
  pressure evicts oldest-deadline stashes. Sequence counters retain the highest
  sent value. Resume remains best effort when older frames have been evicted.

## Frame encoding

A format with `supports_resume` returns the envelope **dict** from its `encode_*`
methods rather than encoded bytes. The transport must stamp `s` onto every frame
regardless. The transport serializes prospective envelopes to measure their
encoded size before committing sequence or replay state. Returning bytes stays
valid; JSON bytes are parsed back for stamping.

`WEBSOCKET_MAX_OUTGOING_BYTES` defaults to 32 MiB per complete message, including
JSON escaping, generated HTML, and the sequence stamp. Oversized output raises
`ValueError` before sending or changing sequence and replay state. Content that
satisfies every field rule can still exceed this aggregate transport capacity.
Transport rejection never truncates a field.

## Narrative field limits

`RenderPlan` and `RenderNode` share block validation. The resolved body and each
display-text field allow 131,072 Unicode codepoints. Titles, list items, and
reference labels preserve their exact accepted text. The complete body must
still fit after viewer resolution. Trees allow 256 total blocks, lists allow
256 items, and nodes allow 256 references.

Machine fields are validated when their records are constructed: tags and
styles allow 64 characters; handles and record identifiers allow 128;
separators allow 4,096; system levels allow 32. References allow 32 affordances
of 64 characters each. Exceeding a field limit raises an error.

Metadata has its own depth budget of eight, 64 entries per mapping, 256 entries
per list, 128-character string keys, and 131,072-character strings. Numbers must
be finite. These rules apply within metadata, independent of enclosing blocks
or transport envelopes. Serialization does not clip fields or reuse metadata
guards to constrain the block tree.

## Batching

A client declaring `caps.batching` gets a burst coalesced into one
`{"t": "batch", "frames": [...]}` flushed at the end of the reactor iteration,
capped at `BATCH_MAX_FRAMES` and `WEBSOCKET_BATCH_BYTES` (default 1 MiB). The byte
target includes the stamped batch wrapper. Groups divide only between complete
envelopes. A legal single envelope above the batch target travels alone. The
batch carries a single `s`; its members carry none. Pending output is flushed
before the stash is taken on close.

## Inbound limits

`AZABAN_MAX_INCOMING_BYTES` defaults to 64 KiB. The selected Azaban codec's budget
is enforced during WebSocket message reassembly and again before JSON decoding.
Exceeding it during reassembly closes the socket with code 1009. Decoded input
has separate depth/string/item bounds; outgoing scenes do not reuse them. `req`/`oob`
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
