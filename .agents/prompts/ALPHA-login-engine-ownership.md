# ALPHA: move login fully into the engine (engine/game cross-validation)

Status: todo (cross-repo: engine + game)

## Context

Unlogged-in login is currently **not engine-owned**, and this is the likely
"should be promoted to the engine" smell. Today:

- The action bridge intercepts all unlogged-in input and returns handled; the
  only engine rule (`evennia/actions/default/loginstart.py:16-37`) just prints
  the connection screen. No `connect` / `create` actions are registered, so
  typed login falls through to `NoMatchAction`.
- `dispatch.py:216` carries a comment referencing a `LoginSessionMixin` on
  `ServerSession` that **exists nowhere in the tree** (grep finds only the
  comment). Stale/aspirational.
- The web/REST path still reaches into
  `commands.default.unloggedin.create_normal_account`
  (`server/inputfuncs.py:329-331`).
- So out-of-the-box telnet login depends on the downstream game's own
  `LoginStartRules` / menu-login override.

The ideal end state (per the user): **login lives fully in the engine**, with
the game supplying only policy/customization, not the core flow.

## Goal

Decide and implement the engine's login ownership: register engine-native
`connect` / `create` / login actions (or an engine login `StateProvider`),
fold the web/REST account-creation path onto the same engine path, delete the
phantom `LoginSessionMixin` comment, and define the clean override seam the game
uses.

## Approach (design + cross-repo validation first)

This needs an engine ↔ game cross-validation pass before code:

1. Read the game's current login implementation (its `LoginStartRules` / any
   `menu_login`-style override) to learn what the engine must provide vs. what is
   legitimately game policy. **This is the one place reading the game repo is
   in scope** (the engine-side audit was deliberately repo-locked).
2. Draw the line: which parts of login are engine machinery (session lifecycle,
   account lookup/create, rate-limiting, the unlogged-in input route) vs. game
   policy (screens, prompts, character-select flow).
3. Propose the engine login surface; get review.
4. Implement engine-side; migrate the web/REST path off
   `default.unloggedin.create_normal_account` onto it; remove the phantom
   comment. Coordinate the game change in the same window.

## Coordination

This interlocks with
[`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md): the
`cmdobj=` login `connect` path and the `unloggedin` cmdset/commands can only be
deleted once login is engine-owned. Land this first, or preserve that path
explicitly until it does.

## Scope boundary

- **In scope:** engine login ownership, web/REST unification, the stale comment,
  the engine↔game line for login.
- **Out of scope:** general cmdset-machinery removal (its own track); broader
  account-system refactors.

## Done means

Telnet and web login both flow through one engine-owned path; the game overrides
only documented policy hooks; no phantom `LoginSessionMixin` reference;
`server/inputfuncs.py` no longer imports from `commands.default.unloggedin`.
