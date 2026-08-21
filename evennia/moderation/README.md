# Moderation

Hard signals only. This package records what a connection reports about itself
and nothing else. There is no scoring, no inference, and no behavioural or
stylistic analysis — every conclusion drawn from these tables is an exact match
on a recorded column, so it can be shown to the player it is used against.

## What exists now

| Piece | Where |
| --- | --- |
| `SessionRecord` model | `models.py`, re-exported from `world.models` |
| Snapshot + write | `capture.py` |
| Address-provenance checks | `checks.py` |
| Session hooks | `server/conf/serversession.py` |

## The write path

Capture is split across two threads on purpose.

`snapshot_session` runs on the reactor and reads only in-memory session state —
no ORM, no Attributes. It returns a plain dict. `persist_snapshot` takes that
dict and does the database work in a worker thread via `defer.background`. The
session object never crosses the thread boundary, and no Attribute is read off
the IO thread.

Both phases swallow their own errors. A failed moderation write must never break
a login or a disconnect.

Two writes per session at most:

- **login** — creates the row once the account and the synced protocol flags are
  both available.
- **disconnect** — stamps the end. A session that never authenticated has no
  login write, so this creates the row instead, which is how probes and failed
  logins get recorded.

`session_uid` is a per-session UUID, not `sessid`: `sessid` is reused after a
restart and cannot identify a row on its own.

## Address provenance

`X-Forwarded-For` is only honoured when the TCP peer is listed in
`settings.UPSTREAM_IPS` (default `["127.0.0.1"]`). When it is not, the recorded
address is the reverse proxy's, and every address-based signal built on it is
void.

Rows carry `peer_ip`, `xff_applied`, and `xff_present` so that failure is visible
in the data rather than silent. `SessionRecord.address_is_trustworthy` is false
when a proxy sent the header and `UPSTREAM_IPS` rejected it.

`checks.report_lines()` reports both the configuration advisory and the recorded
evidence. **Run it before issuing any address-based sanction on a fresh
deployment.** Behind Cloudflare, `CF-Connecting-IP` is the header to trust, not
`X-Forwarded-For`.

## Derived columns

- `cidr` — `/24` for IPv4, `/64` for IPv6. Residential IPv6 rotates the low 64
  bits every lease, so a single `/128` is useless as both an identity and a ban
  unit. This is the unit sanctions should be issued against.
- `ip_hash` — salted SHA-256, set from `settings.MODERATION_HASH_SALT`. It exists
  so address history survives the retention purge of the raw address. **Never
  rotate the salt**; doing so makes every stored hash uncomparable with every new
  one. An empty setting falls back to `SECRET_KEY`, which ties the same problem
  to `SECRET_KEY` rotation instead.
- `client_fp` — SHA-256 over the negotiated capability set: client name, terminal
  type, encoding, and the option flags. Screen dimensions are deliberately
  excluded, since they change whenever the player resizes their window.
- `telnet_sig` — SHA-256 over the option negotiation the client's own stack
  performed: which options it asked for, in which order, and which subnegotiations
  completed. Narrower than `client_fp` on purpose. `client_fp` folds in the client
  name and terminal type, which a player edits in a settings box; changing this
  one means changing client.
- `tls_sig` — SHA-256 over the TLS handshake a reverse proxy terminated:
  protocol version, offered ciphers, curves, and negotiated ALPN. **Not JA4.**
  JA4 also hashes the extension list, which no stock proxy variable exposes, so
  this is named for what it is. Two rules keep it stable: GREASE values are
  stripped, because they are random per connection by design and keeping them is
  why JA3 stopped being useful; and the lists are sorted, because a proxy may
  report them in whatever order it read them and a fingerprint that depends on
  proxy behaviour breaks when the proxy is upgraded. Empty unless the peer is in
  `settings.UPSTREAM_IPS` — a client can set any header, and a forged
  fingerprint is worse than none, because staff would believe it.
- `http_order_fp` — SHA-256 over which headers the client sent and in what order.
  Names only; the values are hashed separately by `http_fp`. Hop-by-hop headers
  are excluded, since a proxy adds and reorders those. Needs no proxy support:
  the websocket handshake already arrives as an ordered list.

## Linking accounts

By exact match on a shared key, not by a confidence number:

- same `device_token`
- same `csessid`
- same `client_fp` **and** same `cidr`
- same `cidr` within a window
- same `cidr` **and** one of `telnet_sig`, `tls_sig`, or `csessid`, where the
  other account is currently blocked

`client_fp` alone is not identity. A default PuTTY or a stock webclient produces
a common value shared by many unrelated players; it is a narrowing signal, never
a conclusion.

The last rule is `detect_identity_correlation`, and every signature the session
carries is checked rather than the first one present — a player who clears
cookies still performs the same TLS handshake, which is the case the handshake
was added for. It fires for an unauthenticated session too, because somebody who
has been banned reconnects and sits at the login prompt with no account name to
match on.

`http_order_fp` is deliberately **not** one of those signatures. Header order
identifies a browser build, so on any busy network it would match most web
players at once, and a flag that fires for everybody teaches staff to skip the
queue. It is recorded, indexed, and readable from the console; it is not a
trigger.

## Not built yet

Nothing on the original list. Sanctions, the staff UI, portal-level blocking,
ASN/geo enrichment, the web device token, negotiation order and timing,
registration signals, and retention purging of the raw `ip` column all shipped;
see `sanctions.py`, `portal_guard.py`, `enrich.py`, `device.py`,
`registration.py`, and `retention.py`. The staff UI is the engine console's
Moderation panel (`evennia/console/panels/moderation.py`).

What is captured and not yet acted on: `tls_sig` and `http_order_fp` are
recorded, indexed, correlated in the account dossier, and reported by the
console's signal-coverage readout. `tls_sig` is a correlation trigger;
`http_order_fp` is not, for the reason above. Neither is used by
`detect_sanctioned_key_reuse`, which matches on a key alone with no network
gate — a build signature there would flag every player who uses the same
browser as somebody banned.
