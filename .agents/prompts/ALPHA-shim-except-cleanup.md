# ALPHA: remove back-compat shims + fix silently-suppressed errors

Status: todo (low-risk)

Two related cleanups from the alpha audit: removable back-compat shims (mostly
pickle-era migration scaffolding), and a handful of `except Exception: pass`
sites that violate the project's own "no silent/suppressed errors" rule. No
external API promises, so the shims can go.

## Security note (do this part deliberately)

Pickle-deserialization surfaces remain **enabled by default on the inter-process
AMP wire** despite having no live caller. Even gated, pickle on a network-facing
boundary is standing attack surface. Removing these is the highest-value item
here.

## Back-compat shims to remove

- **AMP legacy-pickle session/admin path** — gated behind
  `AMP_SESSION_ACCEPT_LEGACY_PICKLE` (defaults `False`; `AMP_SESSION_SERDE`
  defaults `"json"`). Files: `server/amp_serde.py:38-39,122-133,247-260`,
  `server/portal/amp.py:542-554`, settings `636-638`. Justified only by a
  rolling-restart migration that the single up-to-date consumer doesn't need.
  Remove the branch, `session_serde_enabled()` / `accept_legacy_session_pickle()`,
  and the `AMP_SESSION_*` settings.
- **AMP `FunctionCall` RPC** — no in-repo caller, yet keeps a pickle
  deserialization surface enabled by default (Portal↔Server). Remove it (or, if a
  caller is intended, gate it off pickle). This is dead code **and** the main
  attack surface.
- **`amp_serde.py` module docstring lies** — claims "Pickle is no longer used on
  any AMP path" while the paths above still use it. Fix the docstring as part of
  removing the paths (don't leave a true-after-the-fact lie either way).
- **OnDemandHandler legacy-pickle load branch** (`scripts/ondemandhandler.py`) —
  removable back-compat shim.
- **`get_objs_with_attr`** — deprecated force-gated full-scan shim slated for
  removal; confirm no in-repo caller, then drop.

  (`run_async` is also a dead shim but lives in the dead-code batch —
  [`ALPHA-dead-code-batch.md`](ALPHA-dead-code-batch.md).)

## `except Exception: pass` sites to fix

All in newly-added cache/metrics side paths; each swallows real bugs
(AttributeError/TypeError in the called function) with no log. Narrow each to the
expected backend-down exception and `logger.log_trace()` the rest, matching the
sibling sites that already do (e.g. `bus.py`/`queue.py`):

- `utils/idmapper/models.py:557` — `flush_all_keys()` then `except: pass`.
- `typeclasses/attributes.py:82` — `record_attribute_flush_stats(...)` then `except: pass`.
- `comms/models.py:616` — `add_subscriber(...)` then `except: pass` (silent cache desync).
- `comms/models.py:699` — `clear_channel(...)` then `except: pass`.

**Leave alone** (legitimately scoped or acceptable): the launcher top-level
reporters (`evennia_launcher.py:859,1815`), the `cmdhandler`/`evmenu` pipeline
excepts that re-raise on `raise_errors` / `log_trace`, and `managers.py:380`'s
dbref parse. The import-guards above the comms sites are fine — it's the
call-site `pass` that's the issue.

## Approach

Remove pickle paths first (security), confirming `AMP_SESSION_SERDE='json'` and
no consumer sets the legacy flag. Then the other shims. Then the four `except`
sites (trivial, mechanical). One commit per group. TDD where behavior is
asserted; for the `except` fixes, add a test that a non-backend error now
propagates/logs instead of vanishing.

## Scope boundary

- **In scope:** the shims and four `except` sites above.
- **Out of scope:** dead code in [`ALPHA-dead-code-batch.md`](ALPHA-dead-code-batch.md);
  the wider 83-site `except` sweep in [`audits-deferred.md`](audits-deferred.md)
  (this is just the audit-confirmed bug-hiding subset).

## Done means

No pickle deserialization is reachable on the AMP wire; the `amp_serde` docstring
is true; the deprecated shims are gone; the four cache/metrics sites narrow their
catch and log; the suite is green.
