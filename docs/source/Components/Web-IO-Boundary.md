# Web IO Boundary

Evennia's server owns one asyncio IO loop while Django may execute synchronous
views in worker threads. Typeclasses and their handlers remain server-owned:
web workers must not read or mutate live game objects directly.

## Bridge contract

`evennia.web.utils.io.run_on_io_thread` accepts a synchronous callable and
schedules it through the loop bound by `evennia.utils.clock`. The clock callback
enters an isolated Django database scope before invoking the callable.

The bridge executes inline only when the caller is already on the IO thread or
when no loop has been bound during bootstrap and isolated tests. Inline fallback
is not a live-server concurrency guarantee.

Callbacks must be synchronous. Returning a coroutine or other awaitable raises
`TypeError` and disposes of the awaitable.

## Timeout ownership

The timeout exceptions distinguish whether mutation ownership transferred:

- `IOThreadCallTimeout` means the queued callback was cancelled before it
  started. No mutation ran, so a caller may explicitly offer a retry.
- `IOThreadCallIndeterminate` means the callback started and may finish once
  after the worker stops waiting. A mutation caller must report the unknown
  outcome and must not retry automatically.

Read requests may report either timeout as a gateway timeout because completing
the late read has no side effect.

## DTO rule

An IO service accepts scalar IDs, bounded scalar input, and immutable tokens. It
performs lookup, mutable authorization checks, game-state reads or writes, and
serialization within one callback. It returns primitives, safe scalar values,
collections of those values, or frozen data-transfer objects containing only
those values.

Do not return typeclasses, Django models, handlers, querysets, relation managers,
lazy wrappers, requests, or forms. Templates must consume DTO fields rather than
dereference `.db`, handlers, or live object methods.

For mutation routes, repeat authorization and mutable topology checks in the
same callback as the write. A worker-side preliminary check is useful for quick
rejection but cannot authorize the mutation.

## Stock website behavior

### Definite account creation

`DefaultAccount.create_with_provenance()` is the opt-in account-creation API for
IO services that must prove or compensate a complete Account and automatic
Character attempt. It preserves the stock validation, throttling, channels,
hooks, signals, and character-slot flow while returning an
`AccountCreationOutcome` with structured issues and the exact Account/Object
instances constructed by that call. Managers register each instance before its
first save, so the outcome retains a candidate primary key even when a later
save hook fails. Ordinary exceptions anywhere in the opted-in creation core are
frozen into a fault outcome; the legacy API keeps its historical exception
boundary.

The outcome is IO-local and deliberately contains live models. It must never be
returned through a web-worker bridge. The IO service must freshly verify which
candidate rows are durable, validate its complete domain postcondition, and use
public coordinated deletion for exact failed-attempt rows. Candidate IDs are
provenance, not proof that a transaction committed. A caller must not infer
ownership from username, time ranges, or database sequence ranges.

`DefaultAccount.create()` remains the compatibility API and returns its
historical `(account, errors)` tuple. It does not pass the private recorder into
custom character-creation overrides. Services needing definite completion must
explicitly opt into the provenance API and keep the whole verification and
compensation operation on the IO thread.

The stock character list, management, detail, create, update, delete, and puppet
routes use IO services and frozen DTOs. Character forms validate scalar input on
the worker. Services reload the account and character, repeat slug, ownership,
and access checks, perform any mutation, and serialize the result in one
callback. Puppet selection is a POST-only, CSRF-protected mutation.

`CharacterMixin.get_queryset()` preserves the historical downstream `ListView`
contract by returning only characters owned by the requesting account and
matching the view's configured typeclass. Its result is a tuple of frozen
`CharacterListWebDTO` rows, not a Django `QuerySet` or live character objects.
Custom workflows that need fields or behavior beyond that DTO must define a
dedicated IO service instead of dereferencing game state in the web worker.

Aggregate IO services that need the same normal Attributes from many objects
should use `evennia.typeclasses.jsonb_handler.read_attribute_snapshots()`. The
bounded API accepts at most 100 row IDs and 32 keys, reconciles deferred
post-save and durable-spool state under the same fail-closed rules as handler
reads, preserves dirty process-local values, and bulk-fetches committed JSONB
documents in one query. It returns recursively plain snapshots; unsupported
live or serialized object values are rejected rather than crossing into a web
worker. Inputs must be concrete lists or tuples so validation work itself stays
bounded. A complete result is limited to 32 levels, 10,000 decoded values, and
1 MiB of UTF-8 text; exceeding any bound fails the whole snapshot closed.
Reading `db_attrs` directly or inspecting private JSONB row state is not a safe
substitute.

The character page service coalesces its rows and navigation menu into one IO
call. Other authenticated pages load the navigation menu through one fail-soft
IO call; a timeout leaves the menu empty rather than failing the page. Stock
templates consume `detail_url`, `update_url`, `delete_url`, `puppet_url`,
`location_key`, and other scalar DTO fields. They must not dereference `.db`,
relations, or live object methods.

Channel services serialize access, description, subscription count, and URLs.
They return the log filename as a string so logfile reads remain on the worker
instead of blocking the IO loop.

The object-admin account-link action likewise passes only object and actor IDs,
then resolves and performs the complete account/object/capability mutation in
one IO callback.
