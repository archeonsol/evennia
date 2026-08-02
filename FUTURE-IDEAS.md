# Future ideas

Design directions considered but deferred. Not commitments. Sits here so
they don't have to be re-derived from scratch when the moment comes, and
so agents proposing similar changes find the prior thinking first.

Each entry: a short framing, the rough shape, what's been decided to
defer, and any concrete sequencing if/when it gets picked up. Move
entries out when they ship; delete entries explicitly killed.

## Engine stance

The fork's stance lives in
[`core-beliefs.md`](.agents/docs/core-beliefs.md): opinionated,
Underspire-tuned where agnosticism would hurt. This file holds the
operational detail.

### The engine/game line

The test (could a second consumer re-implement it from scratch? if not,
it's engine) lives in [`core-beliefs.md`](.agents/docs/core-beliefs.md);
core-beliefs points back here for the lane breakdown and direction
signals below.

Three lanes:

1. **Engine infrastructure.** Pure seams (cmdhandler, cmdset, locks,
   attributes, AMP, two-process boundary). Settable but not
   opinionated.
2. **Engine defaults with opinions.** Engine code, Underspire-tuned
   defaults, clean opt-out via setting. Examples: trie parser default,
   ftfy normalisation, `@`-prefix convention, switch lowercasing,
   token-boundary matching. Lane-2 test: opt-out via setting must be
   clean. If the only way out is forking, the opinion is too deep.
3. **Game.** Handlers, systems, content, naming policy,
   world-specific commands. Lives downstream.

### Direction signals

**Engine → game (carve out):**

- Single consumer, deep subclass/extension on the game side.
- "Generic shape, game-specific content" pattern.
- Touched only by game code; engine internals don't reference it.
- The five-contrib move (`+underspire.16`) was textbook.

**Game → engine (pull in):**

- Game has accidentally built infrastructure while solving a game
  problem.
- A seam exists in the engine but the implementation lives downstream.
  Phase 2 `AccountCommand` (`+underspire.4`) was this.
- Multiple game-side files re-implement the same primitive slightly
  differently — "this wants to be one thing."

**Stay put (in either direction):**

- "Smell but it works" — watch through one or two refactor cycles
  before acting.
- Boundary requires DB migration, settings rename across consumers, or
  multi-release deprecation. Earns dedicated work, not a hygiene-pass
  impulse.

---

## Plugin system (replacing the `contrib/` namespace)

**Goal:** stop accepting new contribs. Provide a clean seam for
optional, third-party-publishable extensions. Surviving contribs would
in principle migrate to plugins, but in practice they are being deleted
as hygiene passes catch them, so the migration target is shrinking
toward zero.

**Why deferred:** the fork has one consumer. Building a plugin
abstraction before the second consumer exists optimises for an unknown
shape. The contrib namespace is mostly tolerable today and the deletion
work in `+underspire.16` shrank it materially. The next forcing
function (a real second consumer or a third-party plugin author) hasn't
arrived. Note the surviving-contrib count is likely to reach zero by
deletion before a plugin seam is built, so "migrate the survivors" is
not the real driver; third-party plugin authors publishing without
engine PRs are.

### MVP shape (2-3 commits, ~500 LOC, real release)

The seam is mostly an aggregation of settings-list patterns the engine
already has. Single manifest:

```python
@dataclass
class Plugin:
    name: str
    version: str = ""
    typeclass_paths: dict[str, list[str]] = field(default_factory=dict)
    cmdset_paths: dict[str, list[str]] = field(default_factory=dict)
    lockfunc_modules: list[str] = field(default_factory=list)
    inputfunc_modules: list[str] = field(default_factory=list)
    settings_defaults: dict = field(default_factory=dict)
    at_server_start: Callable | None = None
    django_apps: list[str] = field(default_factory=list)
    url_patterns: list = field(default_factory=list)
```

Discovery via `settings.INSTALLED_PLUGINS` list. Hook into
`evennia/__init__.py:_init()` after Django finishes. Each existing
settings-list (`LOCK_FUNC_MODULES`, etc.) gets a plugin-merge step that
appends plugin contributions.

Dogfood case: pick a contrib with real settings-list surface and convert it to
the plugin shape as proof. (`grid/xyzgrid` was the original candidate; it was
removed from the fork once Underspire vendored the parts it used.)

### Real-shape additions (another ~week)

- **pip entry-points** so plugins ship as standalone PyPI packages and
  engine discovers them. This is the "never maintain another plugin"
  win: third parties publish without engine PRs.
- **Plugin version compat check.** Plugin declares
  `requires_engine = ">=6.0.0+underspire.N"`. Engine refuses
  incompatible plugins with a clear message.
- **Settings precedence rules.** Plugin defaults < game settings
  overrides. Recommended approach: post-load shadow via `getattr(
  settings, name, plugin_default)`, not pre-load mutation. Django
  settings are a module not a layered config.
- **Migration story for plugin-defined models.** No programmatic
  solution; document loudly in manifest that plugins shipping models
  carry an uninstall caveat. Same problem upstream contribs have
  today, just more visible.

### What it does NOT solve

- The "is this engine or game?" call. Plugin system gives you a way to
  *answer* it, not make it for you.
- Single-consumer pragmatism. Plugins help future consumers more than
  current ones.
- Existing contribs. They convert one at a time or age out via hygiene
  passes. The policy ("no new contribs") is the easy half.

### Policy questions to settle before building

- Engine-bundled plugins (`evennia/plugins/`) vs separate pip packages?
  Recommended: *both, separate is the recommended shape*. Engine
  bundles a few for batteries-included reasons; everything else is
  separate. `evennia-*` on PyPI becomes the plugin namespace.
- Migration of current contribs? Two-tier: "legacy, supported, no new
  additions." Don't force flag-day migration.
- Plugin versioning under fork? Reuse the `+local` segment.

### Recommended sequence when picked up

1. One spike PR: MVP only, settings-list discovery, one contrib as
   dogfood. Goal: prove the seam end-to-end. Not a release; an
   experiment.
2. Sit on it for a release or two. Use it through Underspire. Find
   where the contract is wrong.
3. Iterate to v1: entry-points, a second dogfood contrib, contract
   docs. Tag the release that formally closes new-contrib additions.

### Risks worth naming

- **Premature abstraction.** Mitigated by spike-first and the option
  to kill the spike if it doesn't feel right.
- **Engine surface growth.** A plugin system is itself engine API and
  the kind where mistakes are expensive to undo. The manifest above
  is deliberately conservative.

---

## Appearance-template slot registry

`AppearanceMixin.appearance_template` is a `str.format` string with
named slots (`{name}`, `{desc}`, `{exits}`, `{characters}`, `{things}`,
`{header}`, `{footer}`, `{extra_state}`). Each slot is filled by a
matching `get_display_<slot>` provider hook. The coupling is
convention-only: adding a slot today means editing the template string
AND adding a `get_display_<slot>` method AND remembering they have to
agree. There is no registry, no validation that every slot has a
provider (or vice versa), and no introspection surface for "what slots
exist."

Rough shape if picked up: a `@template_slot(name=..., provider=...)`
decorator (or a class-level `slots = {...}` declaration) that registers
each slot with its provider, lints at startup that template and
providers agree, and lets game code add new slots without monkey-patching
the template string. Doc generation could then enumerate the slot
surface alongside the other appearance hooks.

Sibling of the **H1** hook registry: H1 declares what `at_*` / `get_*`
/ `return_*` methods exist; this would declare what `appearance_template`
slots exist. Same family of solution (decorator + startup lint + doc
generation), different metadata: H1 covers hook *registration*, this
covers the template-slot *config* coupling. Left as an idea, not
scheduled.

Cross-refs: H1 in
[`decisions.md`](.agents/docs/engine-architecture/decisions.md);
appearance-mixin §2.10 and §3.3 in
[`Typeclass-Hooks.md`](docs/source/Components/Typeclass-Hooks.md).

---

## Live scene fields (ticking readouts over `patch`)

**Goal:** let a viewer's scene carry values that change on their own —
a countdown, an ETA, a dose, a progress bar — so the shell shows a live
instrument instead of a snapshot that lies until the player looks again.
This is the one thing structured clients can do that telnet genuinely
cannot, so it is a real reason to prefer the shell rather than a
cosmetic one.

**What already works.** The whole `patch` path is live end to end and
needs no engine change:

- game emits `viewer.msg(patch=((), {"target": "scene", "ops": ops}),
  session=capable)` (see the occupant deltas in the game's
  `typeclasses/rooms/base.py` and `world/multipuppet/perception.py`)
- `wire_formats/azaban.py` frames it as `{"t": "patch", target, ops,
  meta}` behind the identity scrub and structural limits
- `web/webclient/client/src/main.ts` routes `patch` to
  `scene.apply(target, ops)`
- `client/src/lib/scene.svelte.ts` holds the reactive model and the room
  panel renders it off the log
- `@sync_context` forces a resync

**What is missing** is narrow and in three places:

1. **No slot for it in the scene model.** `Scene` carries `room`,
   `occupants`, `exits`. A timed readout has nowhere to live. Wants a
   generic bag (`fields`, or `panels` keyed by name) rather than a
   bespoke `status` — the second consumer of this will not be a lift.
2. **`scene.apply` handles three op shapes.** Whole-scene `set` on `/`,
   `add` on `/occupants/-`, `del` on `/occupants`. Its own comment says
   "granular sub-path deltas to follow"; a `set` on `/fields/<key>` is
   that follow-up.
3. **Nothing emits on a timer.** Every patch today is event-driven —
   somebody moved. A ticking value is the first *periodic, per-viewer*
   send in the system, and that is the part that needs design rather
   than code.

**Why deferred:** the load shape, not the difficulty. One patch per
second per watching viewer is a new profile for a system whose every
other send is event-driven, and getting it wrong is a broadcast storm
wearing a nice UI. It also has no forcing function yet: the first real
consumer (a moving venue's departure countdown) reads fine as static
text, and telnet keeps that text either way.

**Constraints if picked up:**

- Emit only while a value is actually changing, only to viewers with the
  field on screen, coalesced to at most 1/s, and stop the moment they
  leave or the countdown ends. Prefer a single system tick walking the
  live set over N independent timers.
- Send the *value*, not a re-rendered string. The shell formats it;
  otherwise every format change is a server deploy.
- Fields are viewer-scoped like everything else on this path: no raw
  identity, capability-gated per session, and the same structural limits
  the wire format already enforces.
- Parity is not optional. Every live field needs a static text form that
  stands alone, because that is what every non-shell client gets.

**Sequence:** generic `fields` bag in the scene model and the matching
`set` op in `scene.apply`, with one hand-emitted field to prove the
round trip. Then the periodic emitter, bounded as above, against one
real consumer. Only generalise to a helper after the second consumer
exists and the load profile has been watched under real sessions.

**Engine or game?** Game, by the test in this file — the emitter API
(`msg(patch=...)`) and the wire format both already exist, so a second
consumer could re-implement the ticking part from scratch. What lives
here is the *scene-model contract*: the `fields` shape and the op
vocabulary are shared between the shell in this repo and whatever
emits them, so they get versioned alongside `render.v1` rather than
invented downstream.
