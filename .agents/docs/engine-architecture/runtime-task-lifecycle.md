# Runtime task and database lifecycle

Status: **shipped containment and runtime hardening after `.161`.**

The game server is asyncio-driven but uses Django's synchronous ORM. Django
stores connection wrappers in task-local context under an async loop. A
detached task that touches ORM must therefore own and close its wrapper; process
count, thread count, and `CONN_MAX_AGE=0` do not provide that lifecycle.

## Contract

`clock.run_coroutine(coro, task_kind=...)` is the detached-root authority. It:

1. copies application ContextVars;
2. removes inherited Django wrapper references in the copied context without
   closing the parent's wrapper;
3. runs the coroutine under a bounded task-kind label;
4. closes every root-owned wrapper on success, failure, or cancellation;
5. records active roots, terminal results, and DB-scope closure metrics.

Children that share a logical operation are awaited directly. A separately
scheduled job, command, activity, system, or service operation is a new root.
Cancellation before the wrapper's first step explicitly closes the unstarted
user coroutine.

Long-lived `LoopHandle` roots close database state after every iteration.
Worker-thread jobs reject stale state before work and call
`connections.close_all()` afterwards, so persistent workers retain no pooler
client slots.

The `connection_created` audit reports ORM connections opened by an unmanaged
asyncio context. `ENGINE_RUNTIME_UNMANAGED_DB_POLICY = "error"` promotes that
warning to a CI/runtime invariant once downstream code is clean.

## Scheduler and authorization performance

Synchronous global and online systems execute inline in the scheduler's owned
iteration instead of creating one task-local DB namespace per fire. Only
genuinely awaitable and `all_entities` work creates a system root.

Authorization checks first load a generation-aware resource policy package.
One cold query loads all overrides for a resource; absent operations are
negative-cached. Legacy fallback therefore does not load grants, suspension, or
resource labels. Login prewarms grants and suspension. Warm policy evaluation
is in-memory and performs zero authorization SQL.

## Operational boundary

Deployments stop the running service before replacing engine code or applying
migrations. This releases the prior generation's pooler clients and prevents
old/new ORM contexts overlapping. A failed migration leaves the service down
rather than starting against partial state.

The required production invariant is a stable pooler client count proportional
to active root work, never monotonically increasing with completed tasks.
