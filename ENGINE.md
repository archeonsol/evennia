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
- **`cached_get_display_name`**: `evennia.utils.display_name_cache` (per-looker ndb, TTL + `_recog_generation` / `_sdesc_generation` bumps)
- **Location cmdset cache**: `evennia.commands.location_cmdset_cache` + `_cmdset_generation` invalidation
- **Lock check cache**: `LOCK_CHECK_CACHE_ENABLED` — Command `lockhandler.check` memoized per caller ndb
- **Look prefetch**: `LOOK_ATTR_PREFETCH_ENABLED` — `attributes.get_all()` at start of `at_look`

### Tier 1D–E (ops / startup)

- **`defer_to_worker`**: `evennia.utils.worker_pool` — thread offload for Whoosh, search rebuild, HTTP, etc.
- **Command trace**: `evennia.utils.command_trace` + `COMMAND_TRACE_ENABLED` — `trace_id` per command for structured logs
- **Batched `at_init`**: `evennia.server.at_init_scheduler` — `AT_INIT_BATCH_SIZE`, `AT_INIT_DEFER_ON_RELOAD`
- **GLOBAL_SCRIPTS tiers**: `start_priority: "lazy"` defers non-critical script starts across reactor ticks

### Storage / tick

- Write-behind attributes with `flush_all_dirty()` on the global tick
- Typed attribute columns (`db_int_val`, `db_str_val`, …)
- Optional Redis L2 attribute backend (`RedisCachedModelAttributeBackend`)
- Command access cache (`CMD_ACCESS_CACHE_ENABLED`, `evennia.commands.cmd_access_cache`)
- Flush metrics / Prometheus (`evennia.typeclasses.attribute_metrics`)
- Optional maintenance safety flush (`ATTRIBUTE_FLUSH_ON_MAINTENANCE`)

## Attribute write-behind contract

1. **Hot path:** `Attribute.value` setter and `ModelAttributeBackend.do_update_attribute` mark rows dirty; they are **not** written immediately.
2. **Flush:** Call `evennia.typeclasses.attributes.flush_all_dirty()` on a fixed interval (game global tick, typically 1s). Returns `{"backends": N, "orphans": M, "total": N+M}`.
3. **Crash / tick stall:** Unflushed attrs are lost back to the last flush. Enable `ATTRIBUTE_FLUSH_ON_MAINTENANCE = True` for a 60s safety net in `server_maintenance` (in addition to the game tick).
4. **Production:** Use **PostgreSQL** (not SQLite) when write-behind is enabled.
5. **Redis L2:** Optional read cache; PG remains source of truth. Enable with `ATTRIBUTE_REDIS_CACHE_ENABLED` and `ATTRIBUTE_BACKEND_CLASS = "evennia.typeclasses.redis_attr_cache.RedisCachedModelAttributeBackend"`.
6. **Stale display names:** Bump `bump_recog_generation(viewer)` / `bump_sdesc_generation(character)` (or `invalidate_display_name_cache`) when recognition or visible sdesc changes; game hooks in `world/engine_cache.py`.
7. **Metrics:** `ATTRIBUTE_FLUSH_METRICS_EVERY_N_TICKS` + `maybe_log_flush_metrics(stats, tick_count)`. `ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD` logs tick backlog. With `django-prometheus` + `ENGINE_PROMETHEUS_METRICS_ENABLED` (default on), scrape `/metrics` for:
   - `evennia_attribute_flush_total`, `evennia_attribute_flush_backends_total`, `evennia_attribute_flush_orphans_total`
   - `evennia_attribute_dirty_pending` (gauge, pre-flush backlog)
   - `evennia_attribute_flush_duration_seconds` (histogram)
   - `evennia_cmd_access_cache_hit_total`, `evennia_cmd_access_cache_miss_total` (when `CMD_ACCESS_CACHE_ENABLED`)

## Game repository

The game (`underspire` / `mootest`) depends on this fork via `-e ../evennia` (monorepo) or CI checkout to `./evennia`. Do not install PyPI `evennia==6.0.0` alone for production.
