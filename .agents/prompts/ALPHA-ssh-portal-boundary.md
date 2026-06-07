# ALPHA: resolve the ssh.py ORM-in-Portal boundary leak

Status: todo

## Context

`evennia/server/portal/ssh.py` runs in the **Portal** process but imports
`AccountDB` (`ssh.py:46`) and performs auth inline:
`AccountDB.objects.get_account_from_name(username)` + `account.check_password()`
in `requestAvatarId` (`ssh.py:364-398`), registered via
`factory.portal.registerChecker(AccountDBPasswordChecker(...))` (`ssh.py:530`).

This is the one remaining ORM bleed into the Portal — which is supposed to be a
dumb connector with no game/DB knowledge, auth delegated to the Server over AMP.
It forces Django/ORM into the network-facing process. Inherited from base
Evennia. `SSH_ENABLED` defaults `False`, so it is latent, not live.

## Goal

Decide and implement: either **remove `ssh.py`** entirely (if SSH is not a
supported alpha transport), or **delegate its auth to the Server over AMP** so
the Portal stops touching the ORM, matching how every other portal protocol
authenticates.

## Approach (decision first)

1. Confirm SSH is not a supported/used transport for alpha (it defaults off). If
   nobody needs it, removal is the clean answer — drop `ssh.py`, the
   `SSH_ENABLED` setting, and any wiring.
2. If SSH must stay, move account lookup + password check behind an AMP call to
   the Server (mirror the existing portal→server auth path) so no ORM import
   survives in `portal/`.
3. Either way, add a guard/test that `evennia/server/portal/` imports no Django
   ORM (this was the cleanest boundary the audit found otherwise — worth locking
   in).

## Scope boundary

- **In scope:** `ssh.py` (remove or refactor) and a portal-ORM import guard.
- **Out of scope:** broader AMP/auth redesign; other transports.

## Done means

`evennia/server/portal/` contains no Django ORM access; SSH is either gone or
authenticates over AMP; a test enforces the no-ORM-in-portal boundary.
