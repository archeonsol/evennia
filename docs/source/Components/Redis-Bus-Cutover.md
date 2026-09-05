# Redis bus cutover and rollback

This transport change requires a coordinated Portal and Server upgrade. Mixed
versions are unsupported. Schedule a maintenance window and stop both processes
through the deployment's service manager. Verify that both process IDs have
exited and disable any supervisor that would restart them during maintenance.
Keep the configured Redis instance running.

Record the installed engine and game revisions before changing them. Upgrade
both together. Preserve Redis stream contents, job lists, browser resume state,
and unrelated consumer groups. The bus starts from a fresh stream tail and
reconciles current sessions; it does not recover retained player actions.

## Scoped group maintenance

Use the installed Python environment containing redis-py. Set these environment
variables explicitly from the deployment's configuration:

* `REDIS_BUS_URL`: the configured bus connection URL, including its database.
* `REDIS_BUS_PREFIX`: the exact configured bus prefix.
* `REDIS_BUS_WORKERS`: comma-separated configured Server worker IDs, such as `0`.
* `REDIS_BUS_MAINTENANCE`: `upgrade` or `rollback`.
* `REDIS_BUS_PEERS_STOPPED`: `yes`, only after verifying both processes stopped.

Run this Python block while both processes remain stopped. It operates on
`PREFIX:s2p` and each `PREFIX:p2s:WORKER`. The obsolete group name is the complete
stream key followed by `:grp`, as used by release `underspire.227`.

```python
import os

import redis

if os.environ.get("REDIS_BUS_PEERS_STOPPED") != "yes":
    raise RuntimeError("Verify Portal and Server are stopped before maintenance")
mode = os.environ["REDIS_BUS_MAINTENANCE"]
if mode not in ("upgrade", "rollback"):
    raise ValueError("Maintenance mode must be upgrade or rollback")
prefix = os.environ["REDIS_BUS_PREFIX"]
workers = os.environ["REDIS_BUS_WORKERS"].split(",")
if not prefix or not all(workers) or len(set(workers)) != len(workers):
    raise ValueError("Supply the exact prefix and distinct nonempty worker IDs")
streams = [f"{prefix}:s2p", *(f"{prefix}:p2s:{worker}" for worker in workers)]
client = redis.Redis.from_url(
    os.environ["REDIS_BUS_URL"], socket_connect_timeout=2, socket_timeout=2
)
try:
    for stream in streams:
        if client.type(stream) not in (b"none", b"stream"):
            raise RuntimeError(f"Expected a stream or absent key: {stream}")
    for stream in streams:
        group = f"{stream}:grp"
        if client.exists(stream):
            client.xgroup_destroy(stream, group)
        if mode == "rollback":
            client.xgroup_create(stream, group, id="$", mkstream=True)
        print(f"{mode}: {stream}")
finally:
    client.close()
```

The script fails on Redis errors. Do not start either process after an error;
correct the cause and rerun while both remain stopped. It never enumerates or
deletes keys by wildcard. Repeating rollback destroys and recreates the expected
groups at their current tails, so a previously existing group cannot retain an
old pending list or delivery cursor. Other groups on the same stream survive.

## Upgrade

Install the coordinated engine and game revisions, run the block in `upgrade`
mode, and start both processes. Check confirmed bus readiness and successful
session reconciliation before admitting players. Check a fresh client connection,
authentication, ordinary input/output, and capability negotiation. PID liveness
alone does not establish readiness.

## Rollback

Stop both processes again and restore the recorded compatible engine and game
revisions. Run the block in `rollback` mode immediately before starting them.
Creating the expected old groups at the current tails is required: the old
transport otherwise creates absent groups at `0` and can deliver retained input.
Never preserve an old expected group's pending entries or cursor during rollback.
The procedure intentionally abandons unconfirmed historical actions; it cannot
undo actions that already ran. Other consumers must not write new gameplay
frames between group maintenance and startup.

If an uncertain lifecycle request stopped Server, the Portal watchdog remains
inhibited until fresh readiness or an explicit successful lifecycle outcome.
Inspect process status and logs, then explicitly start Server if required. Do
not blindly repeat the original action or shutdown request.

## Validation boundary

The opt-in `evennia.server.tests.test_bus_cutover` suite executes this exact
Python block against an isolated Redis instance. It seeds retained and pending
actions, preserves unrelated keys and same-stream groups, and checks the actual
`underspire.227` reader after rollback. Its job test raises a lost-reply error
after real Lua execution and checks queue contents through a separate client.
This validates command scope and committed-operation uncertainty, not Redis
durability configuration or production capacity. The tests require the `.227`
git tag and `EVENNIA_REDIS_SERVER` pointing to a local Redis executable.
