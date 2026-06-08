# Engine metrics / observability surface — re-decide

Status: shipped (commit the metrics surface)

## Decision

**Committed to the minimal surface (option 2).** The audit that produced this
prompt looked only at the `attributes.py` call site. In fact a deliberate
surface already existed: `evennia/server/prometheus_metrics.py` registers
counters/gauges/histograms on the default Prometheus registry (exposed on
`/metrics` via django-prometheus), gated by `ENGINE_PROMETHEUS_METRICS_ENABLED`
(default `True`) and degrading to no-ops when `prometheus_client` is absent. It
covers attribute flush + cmd-access / location-cmdset / channel-subscriber /
redis-attr cache hit-miss pairs. The framing test passes: a second game wanting
ops visibility into the engine's own write-behind/cache internals cannot add
this from outside.

What shipped to make it deliberate rather than accidental:

- Removed the redundant outer `except: pass` at the flush call site. The flush
  now calls `record_attribute_flush(...)` directly. The metrics function is
  safe-by-construction (init gate + `None`-guards + `int()` coercion); the only
  expected condition (prometheus absent) is handled structurally, so a genuine
  metrics bug now surfaces instead of vanishing — consistent with the
  no-silent-errors rule and the no-unfalsifiable-guards rule.
- Collapsed the one-line `record_attribute_flush_stats` forwarder; the
  `attribute_metrics` module now owns only the human-readable log helpers
  (`maybe_log_flush_metrics`, `maybe_warn_pending_dirty`).
- Wired the previously-dead `maybe_warn_pending_dirty` into
  `server_maintenance` next to `maybe_log_flush_metrics`.
- Added `evennia/typeclasses/tests/test_attribute_metrics.py`.

The `attributes.py:82` bullet in [shim/except cleanup](ALPHA-shim-except-cleanup.md)
is resolved here; the other three `except` sites in that prompt are untouched.
