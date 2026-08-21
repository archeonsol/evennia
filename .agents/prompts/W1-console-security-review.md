# W1 console: security review

Status: complete (2026-08-20). Gate on the remaining phase-3 panels.

The implementation plan named this review as a gate rather than a
retrospective, because the four panels behind it -- a Python REPL, a SQL
console, process control, and session watching -- are individually the most
dangerous things the engine has ever served over HTTP.

This reviews what is actually in the tree at `befe6a30b`, not the plan.

---

## The threat model, stated plainly

**`engine.console.access` is equivalent to shell access on the game server.**

That is not a warning about a future panel. It is already true: the Records
lens can edit `AccountDB` rows, the Attributes lens can read any object's
document, and the Authorization panel can mint a break-glass grant. Adding the
REPL only makes the equivalence obvious.

Two consequences follow, and everything below is one or the other.

1. **There are no internal boundaries to defend.** Decision D1 settled that on
   purpose: a permission grid over a REPL is theatre. So the perimeter is the
   only boundary, and it has to hold.
2. **Audit is the only internal control that survives.** If it can be edited,
   suppressed, or simply not written, nothing observable remains.

The single exception is `engine.console.moderation`, which admits a
non-superuser moderator to one panel. That *is* a real boundary and is tested
by route enumeration.

---

## Findings

### S1. The session lasts fourteen days. **Blocking.**

```
SESSION_COOKIE_AGE      = 1209600   (14 days)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
```

A console session is a shell. A fourteen-day shell on a laptop that gets
closed, shared, stolen, or simply left in a café is the largest single risk in
this design, and it is inherited from the player website's defaults where it is
entirely reasonable.

The console cannot lower `SESSION_COOKIE_AGE` globally -- that would log
players out -- so it needs its own idle bound.

**Required before the REPL ships.** Track the last console request per session
and refuse (not merely expire) once it exceeds `CONSOLE_IDLE_TIMEOUT`, default
30 minutes. This is a console-scoped check in `ConsolePermission`, not a change
to Django's session machinery.

### S2. No re-authentication before a dangerous action. **Blocking.**

An open console tab is a loaded gun for as long as it is open. Someone who sits
down at an unlocked machine inherits the whole surface.

**Required.** The REPL, the SQL console, and process control each demand a
password re-entry within the last `CONSOLE_REAUTH_WINDOW` (default 5 minutes)
before executing. Not a capability check -- a *proof of presence*.

This is the control that makes S1 survivable even when someone gets it wrong.

### S3. Cookies are not marked secure. **Blocking for any deployment on TLS.**

```
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE    = False
```

Also a sensible player-website default, and wrong for a console. A console
served over plain HTTP leaks a shell credential to the network.

**Required.** The console refuses to serve at all over a non-TLS connection
unless `CONSOLE_ALLOW_INSECURE` is explicitly set, which exists for localhost
development and says so.

### S4. The scoped header is real but partial. **Accepted, with a note.**

`X-Evennia-Console` is required alongside the session cookie. It genuinely
defends: a custom header is unreadable cross-origin and forces a preflight, so
a forwarded console URL authorizes nobody and a cross-site form post cannot
reach the API.

It does **not** defend against anything running in the console's own origin.
Since the console loads no external assets and no third-party scripts, that
surface is currently empty -- which is the actual reason the header suffices,
and the reason the no-external-assets rule is a security rule rather than a
privacy one.

### S5. Audit immutability is enforced, and verified. **Pass.**

Confirmed against the tree:

```
console.consoleauditevent   writable=False   append-only; no console write path
```

`ConsoleAuditEvent` carries no mutation adapter, so the generic writer refuses
it structurally rather than by convention. The seven models that *are* writable
are an explicit allowlist.

**Residual risk:** an operator with the REPL can write to the audit table
directly through the ORM, and nothing in-process can stop that. Detecting it
requires shipping audit rows off the box. Out of scope here; named so it is a
decision rather than an oversight.

### S6. Session watching is surveillance. **Required control.**

Watching a player's render stream is the most invasive capability in the plan
and the least obviously so, because it looks like a debugging tool.

**Required.** Every watch writes an audit row with permanent retention, and
that row appears in the *watched account's* own audit timeline -- not only the
watcher's. A surveillance capability whose subject cannot discover it was used
is a different product from the one this plan describes.

### S7. No rate limiting on the console API. **Accepted.**

`throttle_classes = []` is deliberate: a leaked console session is bounded by
the live capability re-check and by S1/S2, not by a request budget, and every
list is row-capped already.

The one thing a limit would buy is slowing an automated exfiltration through
the SQL console. S8 covers that better.

### S8. SQL and REPL output are exfiltration channels. **Required controls.**

Both can read every row in the database, including password hashes and
moderation addresses, and neither is covered by the masking the Moderation
panel applies.

**Required.** SQL is read-only with a statement timeout and a row cap, and both
panels record the submitted source text in the audit trail. The point is not
prevention -- an operator with a REPL cannot be prevented -- it is that the
retrieval is legible afterwards.

### S9. Process control on the player origin. **Resolved by default-off.**

Decision D9 settled this: same origin, `CONSOLE_SERVER_CONTROL_ENABLED`
default off, re-auth per invocation (S2). A separate origin buys little against
an adversary who already holds a REPL, and costs a second vhost, cookie domain,
and TLS config.

Deployments wanting true isolation set `CONSOLE_ENABLED=False` on the public
web node and run a console-only internal node. That is a topology, not an
engine feature.

### S10. The feed is authorized but long-lived. **Note.**

`stream.py` checks capability once at connect and then holds the connection
open. A grant revoked mid-stream keeps flowing until the client reconnects.

**Required, cheap.** Re-check on a slow cadence inside the stream loop -- every
30 seconds is far below any useful attack window and costs one cached lookup.

### S11. Login now runs off the IO thread. **Pass, with a caveat.**

`check_credentials` reads three columns and compares hashes. Same lookup, same
`is_active` gate, same hasher, and a test asserts the worker and owner paths
never disagree.

**Caveat carried from that work:** the owner path reads through the idmapper,
so a raw `UPDATE` to `is_active` leaves a stale instance authenticating. Any
admin action that deactivates an account must go through the model, not a
queryset update. The Records lens does, because `AccountDB` mutations go
through the mutation service.

---

## Gate decision

**The four remaining phase-3 panels may ship once S1, S2, S3, S6, S8, and S10
are implemented.** They are all small, and each is a control rather than a
feature.

S5's residual risk, S4's scope, S7, and S9 are accepted and recorded.

## Settings this review introduces

| Setting | Default | Why |
|---|---|---|
| `CONSOLE_IDLE_TIMEOUT` | 1800 | S1. A console session is a shell; a fourteen-day one is indefensible. |
| `CONSOLE_REAUTH_WINDOW` | 300 | S2. Proof of presence before a dangerous action. |
| `CONSOLE_ALLOW_INSECURE` | False | S3. Serving a shell credential over plain HTTP must be deliberate. |
| `CONSOLE_SQL_TIMEOUT_MS` | 5000 | S8. A query that runs forever is a denial of service against the game. |
| `CONSOLE_SQL_MAX_ROWS` | 1000 | S8. Bounds one retrieval. |

## What this review does not cover

The engine's non-console attack surface: the player website, the webclient
protocol, the Portal's network handling, and the REST API. Each predates this
work and none of it changed here.
