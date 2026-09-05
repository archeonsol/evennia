# Job Queue

`evennia.jobs.queue` dispatches registered background jobs through Redis or the
database. Handlers must tolerate retries: queue transitions do not make handler
side effects execute exactly once.

## Redis retry transitions

Leasing moves an entry from pending to the worker's processing list and increments
its attempt count in memory. The original serialized entry is the lease token.
A failed handler returns the updated record to pending, or moves it to the
dead-letter list when its attempt budget is exhausted. Redis retries are immediate.
The database backend uses its configured backoff.

The retry transition prepares its replacement JSON before changing storage. One
Lua script checks that source and destination are distinct lists, finds the exact
original entry, pushes the replacement, and removes one original occurrence.
Other Redis commands cannot interleave these operations. Pending jobs retain
their FIFO ordering through LPUSH and RPOP.

For a unique source entry, repeating a completed transition is a no-op, including
after a lost response or after the replacement is leased again. Existing identical
source duplicates each permit one transition; there is no deduplication ledger.
Missing or invalid lease tokens are logged and cannot publish replacement jobs.

## Permissions and failure boundaries

The Redis connection needs permission for EVAL and the script's TYPE, LRANGE,
LPUSH, and LREM commands on the processing and destination keys, in addition to
the queue's enqueue, lease, completion, and reclaim commands. Denied EVAL leaves
the source unchanged. Both keys must be accessible to the same Redis script.

Lua provides isolation, not rollback. A failed destination append leaves the
original entry in processing. A runtime error after that append, such as denied
LREM, can leave both copies. Transition errors are logged; only a confirmed
completed move emits the dead-letter success message. A lost connection can leave
the caller uncertain whether the transition completed.

These guarantees concern commands against Redis while it is running. Persistence,
replication, eviction policy, and handler side effects have separate durability
constraints.

## Tests

`evennia.jobs.tests.TestRedisJobTransitions` executes the production Lua using
`fakeredis[lua]`. It covers both destinations, repeat calls, stale attempts, lost
responses, invalid storage, duplicate source rows, ordering, and failed-handler
attempt progression. Connection and denied-EVAL failures are injected at the
client boundary. These tests do not validate real Redis ACL enforcement or
behavior under memory pressure.
