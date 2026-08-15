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

The stock character list, management, detail, create, update, delete, and puppet
routes use IO services and frozen DTOs. Character forms validate scalar input on
the worker. Services reload the account and character, repeat slug, ownership,
and access checks, perform any mutation, and serialize the result in one
callback. Puppet selection is a POST-only, CSRF-protected mutation.

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
