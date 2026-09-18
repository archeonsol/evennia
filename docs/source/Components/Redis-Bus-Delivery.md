# Redis Bus Delivery

The bus reads forward from an explicit stream cursor captured before discovery.
It does not reclaim pending consumer-group entries. Every frame carries a writer
incarnation and sequence; ordinary frames also carry the confirmed peer generation.
Missing history, sequence gaps, invalid payloads, and Redis failures invalidate the
generation before queued callbacks can execute. Recovery captures a fresh tail and
performs [confirmed synchronization](Redis-Bus-Recovery.md).

The dedicated bus Redis client disables write retries. Each admitted frame has one
XADD attempt. A lost reply means an uncertain outcome: the action may have run.
Unsent work and scheduled callbacks from failed generations are discarded. Already
running actions are not rolled back. Job retries and browser output resume have
separate contracts and remain supported.

## Capacity

Each direction is bounded by 4,096 entries and 64 MiB of encoded bytes. Ordinary data
admission stops at 3,072 entries or 48 MiB, reserving headroom for control/state.
A single payload may not exceed 8 MiB. A representative 4,096-session snapshot with
WebSocket capabilities and terminal dimensions uses about 2.5 MiB.

Outgoing accounting includes in-flight writes and results awaiting loop settlement.
The writer drains admitted frames in batches of up to 64 through one Redis pipeline, so
a broadcast fan-out costs one round trip per batch instead of one per frame. Incoming
accounting includes the active callback. Reads request one frame at a time; one
additional decoded frame can exist while admission checks its size. One queued loop
drain processes at most 32 frames per turn.

Capacity pressure is local backpressure, not a transport failure: an over-limit frame
is rejected (the publisher sees an unavailable result) and the transport stays online,
so a burst of broadcast fan-out cannot cycle discovery and reconcile sessions.
Mandatory control saturation still fails the generation when a control frame is
rejected for any reason other than capacity. Player saturation rejects locally without
evicting earlier work.

The caps and batch size are tunable with the ``REDIS_BUS_MAX_ENTRIES``,
``REDIS_BUS_MAX_BYTES``, ``REDIS_BUS_DATA_ENTRIES``, ``REDIS_BUS_DATA_BYTES`` and
``REDIS_BUS_WRITE_BATCH`` settings; ``None`` keeps the module defaults above.

Publishing while outgoing occupancy exceeds 200 entries logs an
`outgoing queue pressure` warning with the stream, pending entry count, encoded
bytes, and ordinary data limit. Warnings are limited to once per 60 seconds per
transport, including when occupancy drops and rebounds during that interval.

Healthy ordinary traffic is FIFO. A reserved handshake lane can pass startup output
held until final readiness publication. Lifecycle requests cannot use this lane.

## Results and input

`PublicationResult.admitted` reports local queue admission. Awaiting the result or
registering `.addCallback()` observes Redis publication, not peer application or
command completion. `.addErrback()` observes failure without converting an awaited
failure to success. Callback registration belongs to the main event-loop thread;
observer exceptions are logged and do not prevent other observers from running.
Canceling an awaiter does not cancel the shared publication result.

Portal retains declarative client negotiation while unavailable, but does not queue
player actions or actionable OOB messages. A local notice, limited to once every
five seconds per session, explains that interrupted actions may have run. Current
scene, editor, and UI state refresh through the session recovery hook.

Final shutdown synchronization has its own application acknowledgment and shared
deadline. See [shutdown](Redis-Bus-Shutdown.md).

Mixed bus versions are unsupported. Use the [cutover and rollback
procedure](Redis-Bus-Cutover.md) with both processes stopped.
