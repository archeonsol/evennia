# Future ideas

Design directions considered but deferred. Not commitments. Sits here so
they don't have to be re-derived from scratch when the moment comes, and
so agents proposing similar changes find the prior thinking first.

Each entry: a short framing, the rough shape, what's been decided to
defer, and any concrete sequencing if/when it gets picked up. Move
entries out when they ship; delete entries explicitly killed.

## Engine stance (informal)

> Tight and opinionated. Game-agnostic-ish, but where agnosticism would
> hurt Underspire we tune for Underspire.

The fork is not pretending to be a maximally generic toolkit. It is
Underspire's engine, and aims at game-agnostic shapes only where doing
so doesn't cost the consumer. This sits alongside the core beliefs in
[`.agents/docs/core-beliefs.md`](.agents/docs/core-beliefs.md); when
they conflict, the consumer wins for now. (This is a fork stance, not
an upstream-Evennia stance.)

---

## Plugin system (replacing the `contrib/` namespace)

**Goal:** stop accepting new contribs. Provide a clean seam for
optional, third-party-publishable extensions. Long-term: contribs that
survive hygiene either migrate to plugins or get demoted to
`examples/`.

**Why deferred:** the fork has one consumer. Building a plugin
abstraction before the second consumer exists optimises for an unknown
shape. The contrib namespace is mostly tolerable today and the deletion
work in `+underspire.16` shrank it materially. The next forcing
function (a real second consumer or a third-party plugin author) hasn't
arrived.

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

Dogfood case: convert `grid/xyzgrid` to the plugin shape as proof.

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

1. One spike PR: MVP only, settings-list discovery, xyzgrid as
   dogfood. Goal: prove the seam end-to-end. Not a release; an
   experiment.
2. Sit on it for a release or two. Use it through Underspire. Find
   where the contract is wrong.
3. Iterate to v1: entry-points, wilderness as second dogfood, contract
   docs. Tag the release that formally closes new-contrib additions.

### Risks worth naming

- **Premature abstraction.** Mitigated by spike-first and the option
  to kill the spike if it doesn't feel right.
- **Engine surface growth.** A plugin system is itself engine API and
  the kind where mistakes are expensive to undo. The manifest above
  is deliberately conservative.
