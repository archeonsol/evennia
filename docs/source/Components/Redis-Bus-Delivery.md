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

Each direction is bounded by 256 entries and 32 MiB of encoded bytes. Ordinary data
admission stops at 224 entries or 24 MiB, reserving capacity for control/state.
A single payload may not exceed 8 MiB. A representative 4,096-session snapshot with
WebSocket capabilities and terminal dimensions uses about 2.5 MiB.

Outgoing accounting includes in-flight writes and results awaiting loop settlement.
Incoming accounting includes the active callback. Reads request one frame at a time;
one additional decoded frame can exist while admission checks its size. One queued
loop drain processes at most 32 frames per turn. Mandatory control saturation fails
the generation, settles old work, and waits for connectivity and writer availability
before recovery. Player saturation rejects locally without evicting earlier work.

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
