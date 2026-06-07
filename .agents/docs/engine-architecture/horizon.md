# Horizon (speculative, not committed)

Shapes the engine *could* take, captured so today's decisions don't accidentally
close them off. **Sketch-level only.** No churn estimates, no gates, no
dependencies here: if an item earns those, it graduates to
[committed.md](committed.md). If you're tempted to flesh one out, ask first: is
the motivation real today, or are we designing for hypothetical demand?

## Composable `move_to` (M1) — demoted

`move_to(...)` does lock check, hook chain, location change, contents update,
message broadcast, and exit traversal in one call; suppressing a piece means
digging through kwargs. A builder (`Move(actor, dest).skip_messages().execute()`)
would be tidier. **Demoted from committed work:** the kwargs form works, this is
pure ergonomics, not a need. Revisit only as part of a major version, if at all.

## Typeclass decoupled from the Django model

The headline long-horizon item. Today `DefaultObject` *is* a Django model via
`TypedObject`; every typeclass instance is a DB row, in-memory instances aren't a
thing, testing behavior needs the ORM. **Shape:** composition instead of
inheritance (`Character` has-a `ObjectDB`); persistence becomes separable;
in-memory typeclass instances become valid. Breaks every game ever written;
needs migration tooling and a major version. Payoff: tractable testing, resolved
lifecycle ambiguity, engine internals separable from Django.

## Headless engine mode

Today starting Evennia needs a reactor, a settings module, a game directory, and
a launcher; it can't be imported as a plain library. **Shape:** `import evennia;
evennia.standalone()` gives a working engine with none of that, for testing,
embedding, REPL exploration, and alternative drivers. Probably falls out of
typeclass decoupling (same coupling). Payoff: a Python library that ships a MUD
driver, rather than a MUD driver that happens to be in Python.

## Bottom layer as a separate package

**Shape:** `evennia-core` ships the typed substrate (render pipeline, lock
objects, hook registry, result types, permission algebra, typed attributes,
identity model); `evennia` depends on it and adds the friendly sugar. We shape
modules so the bottom is extractable in principle; we don't split the package
until a substrate-only consumer actually wants it.

## Thought exercises (more speculative)

Real directions we don't yet understand well enough to call good ideas. Captured
so the thought isn't lost; not direction.

- **Reactive / event-sourced engine.** Every state change an event; world state
  the fold of events. Natural answer to multi-puppet, undo, replay, audit,
  AI-training capture. **Caution, with evidence:** `evennia/jobs/` (a job queue)
  and `evennia/events/bus.py` (a `GameEvent` bus) were speculatively built toward
  this shape and are **dead/unconsumed** — exactly the "designing for
  hypothetical demand" this section warns against. Resolve via the
  [jobs/eventbus prompt](../../prompts/ALPHA-jobs-eventbus-boundary.md) before
  treating event-sourcing as a real direction.
- **Out-of-process services.** AI inference / heavy simulation as independently
  scalable services rather than reactor-blocking work. AS1 addresses the symptom
  (threaded I/O), not the single-process assumption. Real when a game hits the
  "this 5-second calc freezes everyone" wall in production.
- **Multi-server / clustering.** Sharding by area/account/region; distributed
  identity, cross-shard messaging, consistency under partition. Real only at the
  single-process ceiling (tens of thousands of concurrent players, generously).
- **Plugin mechanism + language packs.** A real plugin system would let the
  agnostic-by-declining-defaults policy (see [decisions.md](decisions.md)) pay
  off: an English language pack ships the `at_say`/`at_whisper` templates, the
  `Characters:`/`Exits:` labels, pluralization/articles, and the Oxford-comma
  joiner as code, instead of the engine carrying or omitting them. Blocked on the
  plugin mechanism existing; until then, opinion just stays downstream.

## Not in scope even here

"What if Evennia were a different framework" thoughts that aren't useful as
direction: wholesale paradigm replacements beyond the above. Keep this doc to
shapes that extend the engine we have.
