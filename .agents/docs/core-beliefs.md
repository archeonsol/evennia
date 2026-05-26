# Core Beliefs

Design principles that inform implementation decisions in Evennia. When in doubt, these guide tradeoffs.

## Engine is a toolkit, opinionated for Underspire

This is Underspire's engine fork, not maximally generic upstream Evennia. It stays game-agnostic in shape but ships Underspire-tuned defaults where agnosticism would hurt the consumer. Test for any opinion in engine code: a game that didn't want it can opt out cleanly. The default opt-out shape is a setting; for structural or naming conventions that travel with a class (e.g. the Phase 3 `@`-prefix command convention), a subclass override also counts. If the only way out is forking, the opinion is too deep.

The engine/game line: if a hypothetical second consumer could not reasonably re-implement this from scratch, it belongs in the engine. Everything else is game-shaped, no matter how generic it looks. Game-specific systems live downstream. `evennia/contrib/` is a legacy bucket: no new additions, contribs migrate or get deleted as hygiene passes catch them.

See [FUTURE-IDEAS.md](../../FUTURE-IDEAS.md) for the three-lane breakdown (infrastructure / opinionated defaults / game) and the direction signals for carving out or pulling in.

## Think in Python, not SQL

The typeclass system is the right primitive for entity behavior: developers work with Python classes, not database schemas. New entity types come from subclassing, not new tables. Attributes (`db` handler) store arbitrary per-object data without schema changes.

Beyond typeclasses, the fork adds Django models when Attributes don't fit. Three patterns earn a model:

- **Queryable indexed state** — high-cardinality lookups, joins, membership tables, anything where Attribute scans don't cut it.
- **Hot-path counters** — atomic updates that the Attribute layer isn't shaped for.
- **Heavy content** — large text or data you don't want sitting in idmapper cache on every load (document bodies, grid posts, audit logs). The model is a pointer; content stays on disk until asked for.

Models are not a fallback for "this feels structured." Each new model names which pattern earns it.

## Extend through hooks, not patches

Objects define clear hook methods called at specific lifecycle points (`at_object_creation`, `at_init`, `at_pre_move`, `at_look`, etc.). New behavior goes in hook overrides, not by modifying core internals. This keeps custom code predictable and upgrade-safe.

## Compose, don't branch

CommandSets merge using set operations (union, intersection, difference). Adding a CmdSet and then removing it restores the original state. This allows layering complex states (combat + darkness + status effects) without nested conditionals. Prefer composable, removable components over boolean flags.

## Fail closed

The lock system denies access by default. Everything is inaccessible unless explicitly permitted. When designing access checks, start locked and whitelist — don't start open and blacklist.

## Objects carry their own state

Mutable game state lives on objects. Handlers (Attributes, Tags, Locks, Scripts, Commands) attach directly to objects so state and behavior travel together. The idmapper cache guarantees instance identity per DB object so on-object state is reliable.

The fork runs external caches for *derived* state (lock cache, cmd-access cache, display-name cache, location-cmdset cache, trie cache, write-behind attrs, redis attr cache). Caches earn their place with a documented invalidation contract: what fills it, what invalidates it, the upper bound on stale reads. They never hold authoritative state; they accelerate access to authoritative state that still lives on objects.

Reference data (prototypes, registries, YAML lookups, constants) is not state and is not subject to this belief. A registry of "what is a rock" earns the registry pattern when key-based lookup is what callers actually want. Reach for it freely.

## Portal and Server are separate concerns

The Portal handles network protocols and stays running across reloads. The Server handles game logic and can be hot-reloaded. Neither knows the other's internals — they communicate via AMP. Don't leak protocol details into game logic or vice versa.

## The framework should be complete

Evennia includes its own web server, webclient, admin interface, and REST API. All connection methods (telnet, websocket, SSH) use the same game objects. Avoid requiring external services for core functionality.

## Keep the schema simple

Complexity grows through Python objects first. The core schema (`ObjectDB`, `AccountDB`, `ScriptDB`, `ChannelDB`) is intentionally minimal and stable; resist changing it. Domain-specific models are fine when they earn it under one of the patterns named in "Think in Python, not SQL" (queryable state, hot-path counters, heavy content). Earn the model with a real pattern, not structure for structure's sake.
