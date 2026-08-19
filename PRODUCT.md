# PRODUCT.md — Underspire Engine

Durable product truth for this repository. Architecture decisions live in
[`.agents/docs/engine-architecture/`](.agents/docs/engine-architecture/index.md);
design principles in [`.agents/docs/core-beliefs.md`](.agents/docs/core-beliefs.md);
deferred directions in [`FUTURE-IDEAS.md`](FUTURE-IDEAS.md). This file does not
duplicate them.

Some fields below are inferred from the repository and from the console brief
rather than from a full interview, and are marked *(inferred)*.

## Platform

web

## Stack

Python 3.12+, Django 6.0.2+, Twisted (two cooperating processes: Portal for
network protocols, Server for game logic). PostgreSQL in production, SQLite in
CI and development. Redis for the Portal/Server bus and several caches. Django
REST Framework for HTTP APIs. Svelte 5 + Vite for shipped frontends
(`evennia/web/webclient/client`), built assets committed so a game never needs
npm. A small Rust component under `native/`.

## Users

**Engine developers.** Building and maintaining the fork itself. Live in the
Python, run the test suite, read the architecture docs.

**Game operators and staff.** Run a game built on the engine. Answer "is the
server well", "who changed that", "why can this account do that", "should this
flagged account be sanctioned". Mostly technical, not necessarily Python
developers. Today they reach the database through Django admin, in-game
builder commands, and game-specific web tools.

**Players.** Reach the engine only through the game: telnet, SSH, or the web
client. They never see the console.

## Product Purpose

A framework for building text-based multiplayer games. It ships its own web
server, web client, REST API, and administration surface, so a game does not
have to assemble those from external services.

This fork diverged far enough from upstream Evennia to be its own engine:
attributes are a JSONB document rather than rows, cmdsets are retired in favour
of an action engine, authority is namespaced capabilities rather than lock
strings, recurring work runs on one system scheduler, and output flows through
a universal render/deliver pipeline.

## Positioning

Opinionated toolkit, tuned for Underspire where being agnostic would hurt the
consumer, but agnostic in shape: any opinion a second game would not want must
have a clean opt-out. If the only way out is forking, the opinion is too deep.

## Operating Context

A game server runs continuously for months. Its operators work from a desk, on
a laptop or a large monitor, often at night, frequently while something is
already wrong: a player reports a bug, a flag queue has filled up, a deploy
misbehaved, a permission is denying something it should not. The console is
opened *because* of a question, used densely for a few minutes, and closed.

Sessions are long-lived and text is the medium of the product itself, so
operators are already fluent readers of dense monospaced information.

The web worker and the game server fail independently. The console must stay
useful when the game server is down, which is exactly when it is most needed.

*(inferred: the physical scene and session shape come from the brief and the
repository, not from an operator interview.)*

## Capabilities and Constraints

- Console access is one capability and is equivalent to shell access: it
  carries a REPL, a SQL console, and process control. There is no internal
  permission grid to visualize, and pretending otherwise would be theatre.
- Live game state belongs to a single IO-owner thread. Web requests read plain
  rows and cross a bounded bridge to mutate.
- An operation has five outcomes, not two. "Started, outcome unknown, do not
  retry" must be as legible as success.
- No external asset hosts. The console must work on an air-gapped deployment
  and must not leak the existence of a staff session to a third party.
- Frontend build output is committed; a game never runs npm.
- Tables can hold millions of rows. Every list is capped and paged.

## Brand Commitments

The engine has no visual brand of its own to protect. The web client's shell is
a separate surface with a separate audience.

The console is engine infrastructure shipped to any game on this engine, so it
must not carry the downstream game's visual identity: a second consumer would
inherit a look that means nothing to them. *(confirmed with the user.)*

## Evidence on Hand

Real data throughout: every installed Django model, the migration graph, the
effective settings, live health checks. Nothing on the console is illustrative;
if a number appears, the server measured it.

## Product Principles

- Think in Python, not SQL. New entity types come from subclassing.
- Extend through hooks, not patches.
- Fail closed. Deny by default and whitelist.
- One way to do each thing; sugar delegates to one canonical substrate.
- The framework should be complete.
- Caches earn their place with a documented invalidation contract.

## Accessibility & Inclusion

Keyboard-first: every action reachable without a mouse, because the audience
already works this way and the surface is dense. Colour is never the only
carrier of state. Text remains selectable and copyable — an operator's next
step is often pasting an identifier somewhere else.
