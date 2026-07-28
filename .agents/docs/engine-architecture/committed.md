# Committed, not yet built

Decided in principle, not yet in the tree. Each lists current partial state and
the preservation constraints that bind it. Shipped work is in
[decisions.md](decisions.md); speculative shapes in [horizon.md](horizon.md).

## R1. Display pipeline (shipped)

`msg`, `at_say`, `return_appearance`, `get_display_name`, language filters, scene
broadcast all run together; there is no "render to a tree for viewer X" step
separate from "send to viewer X." **Target:** every output path goes through
`render(obj, viewer) -> RenderNode` then `deliver(node, viewer)`; filters are
render transforms; `msg`/`at_say` reimplement on top as sugar. Web clients, AI
consumers, accessibility, offline preview all consume the same render output.

- **Partial today:** the [narrative emote seam](decisions.md) (`EmotePlan ->
  deliver -> EmoteResult`) is a shipped instance of the pattern, scoped to emotes.
- **Depends on:** the hook taxonomy (render-side hook contracts) and the actor
  abstraction (actor-as-viewer for the `viewer` argument) — both shipped.
- **Constraint:** keep render output **structured all the way to the delivery
  boundary**. If it flattens to strings mid-pipeline, headless mode and
  alternative consumers (see horizon) become a partial rewrite.

R1A-R1D are implemented; see [decisions.md](decisions.md) and
[r1-migration-status.md](r1-migration-status.md). Remaining surface-specific
semantic enrichment is additive package/game work, not unfinished core authority.

## R3. Capability authorization (shipped through R3E)

The earlier L1 lock-object proposal was superseded by the capability runtime.
Namespaced capabilities, positive scoped grants, structured policies, generic
resource adapters, split caches, audited recovery, offline differential tooling,
and per-kind write freezing are implemented. See
[r3-authorization.md](r3-authorization.md). R3F removal is intentionally deferred.

<details><summary>Superseded L1 framing</summary>

`"cmd:perm(Builder) and not perm(Quell)"` is parsed at call time; typos are
silent; `check_permstring` ignores quell because there is no scope-aware
resolver. **Target:** `Permission.Builder`, `Scope.Effective`, etc. as objects;
`Lock.cmd(Permission.Builder)` composable and validated at definition time;
`check(actor, perm, scope=...)` resolves quell correctly. String form deprecated
with a migration path (P1).

- **Partial at the time:** the action engine already had predicate objects such
  as `Holds` and `IsSelf`; the capability runtime later superseded the proposed
  global `Lock` / `Permission` replacement.
- **Depends on:** the actor abstraction (shipped) for the `check` first arg.

</details>

## W1. Web / client / protocol modernization

The webclient protocol, REST views, and input/output handler stack are real
public API untouched by the rest of this arc; each predates these principles
(string DSLs, custom hook shapes, direct-to-`msg` coupling). **Target:** each
surface typed and render/deliver-aware (web client receives RenderNodes, not
pre-formatted text), covered by the hook registry, no string DSLs.

- **Depends on:** R1 (render output is what web/AI consumers receive) and H1
  (registry covers these surfaces). Sequenced last.

## I2. Multi-puppet — under review

The engine historically treated multi-puppet as an edge case of single-puppet,
with session relay and per-puppet slots living game-side. **Status: not a
committed target right now.** The [ControlBinding focus stack](decisions.md) may
already subsume the substrate this item wanted. Needs a dedicated pass to decide
whether anything remains (first-class slot primitives, engine-level
broadcast-to-all-my-puppets) or whether the focus stack closes it. Do not build
against the old "multi-puppet as edge case" framing until that review lands. The
same review absorbs the parked `PuppetPolicy`-replacing-`MULTISESSION_MODE`
question (the policy layer of the same cluster).

## Preservation constraints (apply to all the above)

These keep the horizon paths reachable without committing to them. Honor them
when building R1 / L1 / W1:

- **Don't hard-code `ObjectDB` as a parent in new substrate.** Typed primitives
  (RenderNode, Lock objects, result types) should *operate on* objects, not *be*
  Django models. (Honored so far: the action engine is plain Python over objects.)
- **Key off the actor, not raw `AccountDB` / `ObjectDB`.** Permissions, render
  viewers, and hook signatures should reference the actor abstraction so they
  survive an eventual typeclass/model decoupling.
- **Keep the hook registry free of typeclass-model assumptions.** A hook
  registers by signature, not by which class backs it.
- **Keep any new storage primitive backend-agnostic.** The `IAttributeBackend`
  interface honors this (InMemory backend exists), though JSONB leans
  Django-specific and the InMemory path has a known dead-M2M seam to fix; new
  primitives should pick a backend at construction, Django being one option.
