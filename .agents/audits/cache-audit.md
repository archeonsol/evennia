# Cache audit (F8 step 2)

Working notes from reading the seven fork caches side-by-side after step 1
landed the per-cache invalidation contracts. Throwaway: each accepted
finding ships as its own commit + test; delete this file once the last one
lands or the remaining findings are filed elsewhere.

Cache inventory (seven, plus the eighth `channel_subscriber_cache` folded
in):

- `location_cmdset_cache` (engine, in-process LRU)
- `cmd_access_cache` (engine, per-caller `ndb`)
- `cmdparser_trie` (engine, per-cmdset attribute)
- `redis_attr_cache` (engine, Redis L2 over PG)
- `channel_subscriber_cache` (engine, Redis index over M2M)
- lock-check cache (engine, per-caller `ndb` in `LockHandler._check`)
- write-behind attrs (engine, in-process dirty-set + tick flush)
- display-name cache (game-side; engine has only a seam — see F-1 below)

## Findings

Graded `H` (likely worth doing), `M` (worth discussing), `L` (doc-only
caveat). Each links to the originating signal.

### F-1 (L): display-name cache lives game-side; engine seam is undocumented

The `+underspire.36` change moved display-name caching out of the engine.
The engine still exposes whatever hook the game cache attaches to, but
nothing in `evennia/` documents that seam. A future engine contributor
adding a name-mutating path has no in-tree pointer telling them which
hook game-side caches subscribe to.

**Proposal.** Find the engine-side hook(s) the game cache wraps and add a
one-paragraph note at that hook ("downstream caches may key on this; fire
it on any change to the display name") with a cross-reference to the
core-beliefs cache-discipline rule. **Doc-only, no behavior change.**

### F-2 (H, shipped): paired lock-cache + cmd-access invalidation is a footgun

Every permission-mutating call site has to remember to fire **both**
`invalidate_lock_cache(caller)` and `invalidate_cmd_access_cache(caller)`.
Today the two known call sites do this:

- [`commands/default/account.py:1035-1039`](../../evennia/commands/default/account.py#L1035) — `@quell`/unquell
- [`commands/default/admin.py:554-555`](../../evennia/commands/default/admin.py#L554) — `@perm`

Any third caller (game-side or future engine code) that mutates
permissions and forgets one of the pair will see stale lock-check
results or stale cmd-access results. The two caches share the same
trigger ("this caller's permissions changed"); the API doesn't.

**Proposal.** Add a single `invalidate_caller_access(caller, *, sessions=())`
helper (probably in `evennia/locks/` or a new `evennia/utils/cache_invalidate.py`)
that fans out to both caches and to any future caller-permission cache.
Migrate the two known call sites. **Behavior-neutral on existing paths;
adds a single seam that future callers can use.**

### F-3 (M, shipped writeup): redis-attr TTL is 60× the actual staleness bound

Maintenance loop ticks every 60s ([`server/service.py:519`](../../evennia/server/service.py#L519)),
and `ATTRIBUTE_FLUSH_ON_MAINTENANCE` defaults on, so cross-process
staleness from write-behind is **bounded by 60s**, not by the
`ATTRIBUTE_REDIS_CACHE_TTL = 3600`.

Two readings:

1. The TTL is the long-tail safety net for orphaned keys (no explicit
   invalidation fires) — that's a real role, keep it.
2. The TTL is dead weight because every real write path explicitly
   invalidates or republishes.

The audit can't tell which without grepping every Redis-write path for
"are there any keys that escape both `_cache_drop`, `invalidate_attrs`,
and `flush_dirty` republish?" If there are, TTL stays. If there aren't,
TTL could go to a much smaller number (5-10 min) and surface bugs faster.

**Conclusion (after writeup).** TTL is **load-bearing**, keep it. Three
real escape paths exist:

1. `_cache_drop_object` is defined but no caller — owner deletion does
   not proactively flush the per-owner Redis index.
2. `Script.delete()` does **not** call `self.attributes.clear()`, unlike
   ObjectDB/AccountDB/ChannelDB. Every deleted Script leaks its
   attribute keys to TTL (see F-8).
3. Any downstream typeclass override of `delete()` that skips
   `attributes.clear()` leaks the same way.

Without TTL, Redis would grow unboundedly with stale keys. The 3600s
default is generous; lowering to 600s would surface escape-path bugs
~6× faster but the cost of an escape is small (a few KB of stale keys
per deleted owner) and bug-surfacing isn't urgent. Keeping 3600s and
documenting *why* in the cache docstring is the right shape.

Findings surfaced during this writeup are tracked as F-8 (Script.delete
fix) and F-9 (pre_delete receiver for `_cache_drop_object`).

### F-4 (H, shipped): channel-subscriber cache has no Account/Object-delete hook

`channel_subscriber_cache` invalidates on subscribe/unsubscribe/clear-channel
and self-heals on `ObjectDoesNotExist` in `get_cached_subscribers`. There
is **no hook on `Account.delete()` or `Object.delete()` that proactively
removes refs from every channel the deleted entity was subscribed to.**

Consequence: a deleted account's `a:<pk>` ref stays in every channel set
until each channel is independently fan-out-checked, hits the stale ref,
and triggers a per-channel rebuild. Until then the cache returns the ref,
`_resolve_refs` filters it on the next pg-batch lookup (good), but the
SREM never happens proactively. On a server with many channels, the
stale-ref count grows and each rebuild costs a full DB read.

**Proposal.** Wire a `pre_delete` / `at_delete` hook on Account and
Object that calls something like `remove_subscriber_from_all_channels(entity)`.
Implementation: iterate channels the entity subscribes to (DB read once)
and SREM the ref from each. Test: delete an account, assert no `a:<pk>`
ref remains in any channel's Redis set.

### F-5 (L): trie cheap-key in-place-mutation footgun

The trie's two-tier cache (`(len, sum(id))` followed by structural
signature) does **not** detect in-place mutation of a cached command's
`key`/`aliases` on a reused cmdset object. The docstring now warns about
this; production code rebuilds the cmdset on any cmd change, so the
caveat doesn't bite there.

The audit pass should grep for any in-tree caller that mutates command
attributes in place on a reused cmdset. If zero, this stays as the
documented warning. If non-zero, those callers need the
`del cmdset._trie_command_trie` line or the cheap key needs widening.

**Proposal.** Grep + report. **Doc-only unless the grep finds callers.**

### F-6 (L, shipped): `CMD_ACCESS_CACHE_ENABLED` defaults off, `LOCK_CHECK_CACHE_ENABLED` defaults on

Both are per-caller `ndb` caches with similar invariants and the same
invalidation triggers (per F-2). The asymmetric defaults look historical
but the audit can't tell the reason from code alone.

**Proposal.** Flag for user decision: is `CMD_ACCESS_CACHE_ENABLED = False`
intentional (conservative default for downstream games whose Command
classes might do non-cacheable work in `access`)? If yes, the prompt-level
docstring should say so. If no, flip the default and add a settings note.
**Decision-only, no code change in step 1 of this finding.**

### F-7 (M): no cross-cache layering doc — lock-check cache is partly redundant under cmd-access

When `CMD_ACCESS_CACHE_ENABLED=True`, the cmd-parse hot path goes
`cached_cmd_access` → `cmd.access(caller, "cmd")` → `LockHandler.check`.
A cmd-access hit short-circuits before the lock-check cache runs. A
cmd-access miss falls through and the lock-check cache catches the
inner call.

**The two caches are not redundant in general** (lock-check is used
everywhere: visibility, traversal, contrib code), but on the cmd-parse
hot path there is a layering relationship that nothing documents. A
contributor benchmarking lock-check might not realize cmd-access sits
in front of it on the hot path and produces almost all the hits.

**Proposal.** One paragraph in the lock-check cache docstring noting the
cmd-parse path is fronted by cmd-access (when enabled). **Doc-only.**

### F-8 (M, shipped): `Script.delete()` doesn't call `attributes.clear()` — leaks Redis attr keys

ObjectDB, AccountDB, and ChannelDB all call `self.attributes.clear()`
in their custom `delete()` before `super().delete()`, which routes per-attr
through `do_delete_attribute` → `_cache_drop`. **`Script.delete()` does
not** (`scripts.py:540-556`), so every deleted Script leaves its Redis
attribute keys orphaned until TTL.

Surfaced while writing F-3.

**Proposal.** Add `self.attributes.clear()` before `super().delete()` in
`Script.delete`. Trivial one-liner. Test: delete a Script with at least
one attribute; assert `_cache_drop` ran for that attribute (or, with a
fake redis, assert the delete pipeline got called).

### F-9 (M, shipped): no `pre_delete` receiver wires `_cache_drop_object` — owner deletion is per-attr instead of per-owner

`_cache_drop_object` exists on `RedisCachedModelAttributeBackend` but no
caller invokes it. Today owner deletion (when it works) clears Redis
one attribute at a time via `attributes.clear()` → N `_cache_drop`
calls → N Redis round-trips. The single per-owner drop would be one
round-trip per delete and also covers downstream typeclasses that
override `delete()` without calling `attributes.clear()` (and Script
until F-8 lands).

Same shape as F-4 for the channel-subscriber cache: a `pre_delete`
receiver on the four owner classes (ObjectDB, AccountDB, ScriptDB,
ChannelDB) that calls the per-owner cache drop. Surfaced while
writing F-3.

**Proposal.** Add `pre_delete` receiver in `evennia/typeclasses/models.py`
(or wherever TypedObject lives) that builds a transient backend from
`(model, obj.pk)` and calls `_cache_drop_object`. Mirrors the F-4 wiring
pattern (catch-all receiver bails on first hasattr check).

## Items considered and dropped

- **Unify location-cmdset and trie cache as "two views on the same merged
  cmdset."** They share the trigger (cmdset stack change) but the storage
  shapes are unrelated (LRU vs per-cmdset attribute) and the location
  cache's key includes the *caller*, not just the cmdset. Forcing them
  into one shape buys nothing.
- **Add a settings switch for redis-attr `nx_only`.** The NX semantics
  exist for TOCTOU between PG read and Redis publish; turning them off
  would reintroduce the phantom-data window the cache contract
  explicitly avoids. Not a knob worth offering.

## Recommended order

If you want to do these in dependency order:

1. **F-2** (helper) — small, blocks nothing, makes F-6's decision easier
   to reverse if you flip the cmd-access default.
2. **F-4** (channel-subscriber delete hook) — independent, bug-shaped.
3. **F-6** (cmd-access default decision) — needs your call before any
   code change.
4. **F-3** (TTL audit) — write-up only; decide if TTL stays after.
5. **F-8** (Script.delete fix) and **F-9** (pre_delete receiver) — surfaced
   during F-3; each its own commit + test.
6. **F-1, F-5, F-7** — doc-only batch; can bundle into one commit.
