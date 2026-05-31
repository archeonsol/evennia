# Underspire Engine

Fork of Evennia 6.x maintained for **UNDERSPIRE**. The Python package remains `evennia` for compatibility (`import evennia`, `evennia start`).

## Install

```bash
pip install -e /path/to/this/repo
pip install -e /path/to/this/repo[game]   # game-server deps (Redis, DRF, etc.)
```

Requires **Django 6.0.2+** and **Python 3.12+**.

## Fork features

### Tier 1 (command / messaging hot path)

- **`msg_contents`**: precomputed display names, `display_names` passed to actor-stance parser; skips `$` parse when message has no funcparser tokens; `get_message_recipients()` hook on locations
- **Location cmdset cache**: `evennia.commands.location_cmdset_cache` + `_cmdset_generation` invalidation
- **Lock check cache**: `LOCK_CHECK_CACHE_ENABLED` — Command `lockhandler.check` memoized per caller ndb
- **Look prefetch**: `LOOK_ATTR_PREFETCH_ENABLED` — `attributes.get_all()` at start of `at_look`

### Tier 2 (subsystem modernization)

- **AMP session serde:** `AMP_SESSION_SERDE = "json"` for Msg* traffic (no pickle on hot path). Admin/sync still uses pickle. Reject legacy pickle unless `AMP_SESSION_ACCEPT_LEGACY_PICKLE = True`.
- **Event bus:** `evennia.events.emit(subject, payload, actor=..., persist=...)` — sanitized JSON payloads; optional Redis stream + `GameEvent` Postgres rows (`EVENT_BUS_BACKEND`, `EVENT_BUS_PERSIST_SUBJECTS`).
- **Job queue:** `evennia.jobs.enqueue_job(type, payload)` — **registry-only** callables (`JOB_QUEUE_REGISTRY`); Redis list or `EngineJob` table; drain via `JOB_QUEUE_DRAIN_EVERY_N_TICKS` on global tick.
- **Channel subscriber cache:** Redis SET per channel (`CHANNEL_SUBSCRIBER_CACHE_ENABLED`); PG M2M remains source of truth; invalidates on subscribe/unsubscribe.
- **Postgres tooling:** `evennia.server.database_postgres.apply_postgres_engine_defaults(DATABASES)` — `CONN_MAX_AGE`, health checks, statement timeout. Read replicas for website/logs only (not game thread).

**Not implemented (by design):** WebSocket typing/presence/draft OOB.

### Tier 2.5 (game + engine hooks)

- **Room scene index:** `evennia.objects.scene_index` — Redis SET per room; `DefaultObject.get_message_recipients()` uses it when `ROOM_SCENE_INDEX_ENABLED`.
- **Audit:** `evennia.events.emit` via `mootest/world/audit.py` (economy, staff pending, ban/unban, `@perm`).
- **Jobs:** extended `JOB_QUEUE_REGISTRY` in game settings (indexes, channel cache rebuild, event export, Discord webhook).
- **Channel cache:** PG M2M is truth; Redis rebuilt on start via `channel_subscriber_cache_rebuild` job.

### Tier 1D–E (ops / startup)

- **`defer`**: `evennia.utils.defer` — `in_thread` / `background` / `threaded`, thread offload for Whoosh, search rebuild, HTTP, etc.
- **Command trace**: `evennia.utils.command_trace` + `COMMAND_TRACE_ENABLED` — `trace_id` per command for structured logs
- **Batched `at_init`**: `evennia.server.at_init_scheduler` — `AT_INIT_BATCH_SIZE`, `AT_INIT_DEFER_ON_RELOAD`
- **GLOBAL_SCRIPTS tiers**: `start_priority: "lazy"` defers non-critical script starts across reactor ticks

### Storage / tick

- Write-behind attributes with `flush_all_dirty()` on the global tick
- Typed attribute columns (`db_int_val`, `db_str_val`, …)
- Optional Redis L2 attribute backend (`RedisCachedModelAttributeBackend`)
- Command access cache (`COMMAND_ACCESS_CACHE_ENABLED`, `evennia.commands.cmd_access_cache`)
- Flush metrics / Prometheus (`evennia.typeclasses.attribute_metrics`)
- Optional maintenance safety flush (`ATTRIBUTE_FLUSH_ON_MAINTENANCE`)

## Attribute write-behind contract

1. **Hot path:** `Attribute.value` setter and `ModelAttributeBackend.do_update_attribute` mark rows dirty; they are **not** written immediately.
2. **Flush:** Call `evennia.typeclasses.attributes.flush_all_dirty()` on a fixed interval (game global tick, typically 1s). Returns `{"backends": N, "orphans": M, "total": N+M}`.
3. **Crash / tick stall:** Unflushed attrs are lost back to the last flush. Enable `ATTRIBUTE_FLUSH_ON_MAINTENANCE = True` for a 60s safety net in `server_maintenance` (in addition to the game tick).
4. **Production:** Use **PostgreSQL** (not SQLite) when write-behind is enabled.
5. **Redis L2:** Optional read cache; PG remains source of truth. Enable with `ATTRIBUTE_REDIS_CACHE_ENABLED` and `ATTRIBUTE_BACKEND_CLASS = "evennia.typeclasses.redis_attr_cache.RedisCachedModelAttributeBackend"`.
6. **Metrics:** `ATTRIBUTE_FLUSH_METRICS_EVERY_N_TICKS` + `maybe_log_flush_metrics(stats, tick_count)`. `ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD` logs tick backlog. With `django-prometheus` + `ENGINE_PROMETHEUS_METRICS_ENABLED` (default on), scrape `/metrics` for:
   - `evennia_attribute_flush_total`, `evennia_attribute_flush_backends_total`, `evennia_attribute_flush_orphans_total`
   - `evennia_attribute_dirty_pending` (gauge, pre-flush backlog)
   - `evennia_attribute_flush_duration_seconds` (histogram)
   - `evennia_cmd_access_cache_hit_total`, `evennia_cmd_access_cache_miss_total` (when `COMMAND_ACCESS_CACHE_ENABLED`)
   - `evennia_location_cmdset_cache_hit_total`, `evennia_location_cmdset_cache_miss_total` (when `LOCATION_CMDSET_CACHE_ENABLED`)
   - `evennia_channel_subscriber_cache_hit_total`, `evennia_channel_subscriber_cache_miss_total` (when `CHANNEL_SUBSCRIBER_CACHE_ENABLED` and Redis reachable)
   - `evennia_redis_attr_cache_hit_total`, `evennia_redis_attr_cache_miss_total` (when `ATTRIBUTE_REDIS_CACHE_ENABLED`)

## Game repository

The game (`underspire` / `mootest`) depends on this fork via `-e ../evennia` (monorepo) or CI checkout to `./evennia`. Do not install PyPI `evennia==6.0.0` alone for production.
