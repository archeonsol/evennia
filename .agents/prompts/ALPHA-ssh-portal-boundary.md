# RECONSIDER: ssh.py ORM-in-Portal boundary leak

Status: deferred (2026-06-13). No live problem; revisit only on a trigger below.

## Why deferred

`ssh.py` is the one place in `evennia/server/portal/` that imports the ORM
(`from evennia.accounts.models import AccountDB`, `ssh.py:46`) and does inline
auth (`AccountDB.objects.get_account_from_name` + `account.check_password` in
`requestAvatarId`). But it is **not active**: `service.py` only imports `ssh.py`
inside `register_ssh()`, which only runs when `SSH_ENABLED` is True. `SSH_ENABLED`
defaults False, so in the default config `ssh.py` is never imported and that ORM
access never executes in the Portal. The boundary is latent, not violated.

So there is no bug to fix today. The earlier "remove the one remaining ORM bleed"
framing oversold it; this is split below into the part that is a concrete win and
the part that is principle-driven.

## Actual benefit (concrete, not opinion)

- **Dead-code removal.** `ssh.py` is ~550 lines of off-by-default, unused,
  unmaintained transport. Removing it (plus its settings and the website advert)
  is the same rationale as the rest of the cleanup release: delete dead weight.
- **Possible dependency trim.** SSH pulls in `twisted.conch` (+ `cryptography`,
  `pyasn1`). If nothing else uses them, removal lets the maintainer drop those
  from `pyproject.toml` (a package-file change for the human, not the agent).

## Opinionated (principle-driven, weak ROI right now)

- **The no-ORM-in-Portal static import guard.** A test asserting `portal/`
  imports no Django ORM cannot pass while `ssh.py` carries its import, so removal
  "unblocks" it. But the guard only pays off against a *future* contributor who
  reintroduces an ORM import. In a solo alpha that contributor does not exist; it
  is defensive programming upholding the documented "Portal is a dumb connector"
  belief (see [Core Beliefs](../docs/core-beliefs.md)), not a fix for anything
  broken. Legitimate, but optional.
- **The "footgun" of `SSH_ENABLED=True` reintroducing ORM-in-Portal** is real but
  only fires if someone deliberately enables an off-by-default feature.

## The fork, when revisited

1. **Remove.** Bank the dead-code cleanup; the static boundary guard comes free
   since the violating file is gone. Pick this if SSH is confirmed unwanted.
2. **Keep + fix.** Delegate auth to the Server over AMP (a new "validate creds"
   AMP request/response; Conch's `requestAvatarId` can return a Deferred that
   fires on the round-trip), plus a "pre-authenticated session" handoff so the
   Portal stops holding an `AccountDB` object. ~day of work; the risk concentrates
   in the pre-auth session minting and Conch realm plumbing, both hard to
   integration-test. Only worth it if SSH is a transport you actually want.
3. **Stay deferred.** No harm; SSH remains off and dormant.

## Reconsider when

- SSH becomes a desired connection transport (then do option 2), **or**
- the project gains contributors (the static guard's regression-prevention value
  rises), **or**
- a broader `portal/` ORM-boundary cleanup is undertaken and this folds in.

## Blast radius (so it is not re-derived)

If removing (option 1):

- **Delete:** `evennia/server/portal/ssh.py`.
- **`settings_default.py`:** `SSH_ENABLED`/`SSH_PORTS`/`SSH_INTERFACES` (~70-75),
  `SSH_PROTOCOL_CLASS` (~1394-1395), and the SSH mention in the comment at ~1370.
- **`server/portal/service.py`:** the `"ssh": []` info entry (~40), the
  `SSH_ENABLED` check (~82-83), the `register_ssh` method (~143-171).
- **`web/utils/general_context.py`:** `SSH_ENABLED`/`SSH_PORTS` globals and the
  `ssh_enabled`/`ssh_ports` context keys (~31-32, 57, 79-80, 131-132).
- **`web/templates/website/homepage/main-content.html`:** the `{% if ssh_enabled %}`
  block (~39-44).
- **Tests:** drop SSH expectations in `web/utils/tests.py` (~49-50) and
  `server/tests/test_launcher.py` (~44); add the static portal-no-ORM guard.
- **Leave alone:** incidental "Telnet and SSH" comments in `session.py`,
  `serversession.py`, `sessionhandler.py`, `ssl.py`, `wire_formats/base.py`.
- **Flag, do not edit:** whether `twisted.conch`/`cryptography`/`pyasn1` become
  unused (a `pyproject.toml` decision for the maintainer).

## Done means

A decision is made on the fork above, or this stays deferred until a trigger fires.
