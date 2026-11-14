# Web Mutation Bridge

Stock Django pages validate forms on worker threads, while Evennia game state
belongs to the bound IO owner. The admin and authentication bridges preserve
that split without granting workers a persistence exception.

## Admin protocol

`evennia.console.services` contains a frontend-neutral closed registry for Account, Object,
Channel, Script, HelpEntry, Msg, and ServerConfig. Each entry allowlists its
concrete fields, foreign keys, relations, inline Tags, lifecycle adapter, and
delete behavior. Django admin and the operations console both use it; it is not
a generic model-save API.

The worker validates the ModelForm and every formset, then encodes only:

- exact immutable scalar types;
- bounded built-in containers frozen without copy or serialization protocols;
- foreign-key and relation primary keys;
- explicit old/new Tag operations; and
- repr-hidden password fields in dedicated requests.

Models, querysets, handlers, lazy values, callables, cycles, non-finite
numbers, arbitrary subclasses, and over-limit payloads reject before dispatch.

`OwnerSafeModelAdminMixin` returns `form.instance` from `save_form()` without
calling custom form persistence. `save_model()` stages the parent. Once all
formsets validate, `save_related()` releases the worker connection and sends
the complete request to the owner. This prevents Account creation forms from
calling password hooks on the worker and prevents parent, relation, or inline
writes from splitting across owners.

Django's `ModelAdmin.changeform_view()` and `UserAdmin.add_view()` normally
open worker-connection transactions. Registered game admins bypass only those
wrappers while retaining Django's existing `_changeform_view()` and
`UserAdmin._add_view()` orchestration, CSRF checks, permission gates, form
rendering, messages, and response behavior. No worker transaction is presented
as atomic with the owner connection.

## Owner operation

The owner callback always reloads the actor and requires a live, active Account.
It then applies the authority model named by the frontend. Django admin repeats
fresh staff, model-permission, and superuser checks; Account authority fields
and relations still require a current Django superuser there. The operations
console repeats active-account state under its already-live,
shell-equivalent `engine.console.access` capability and does not invent a
second Django-permission boundary. Both paths validate target and related IDs,
uniqueness, supported inline kinds, and payload bounds before the first write.

Creation uses established lifecycle helpers. Account creation uses
`create_with_provenance()`. Object, Channel, Script, Msg, and HelpEntry helpers
offer private recorder seams that retain the exact constructed instance before
its first save. A later helper, hook, relation, or signal failure can therefore
be classified from its actual durable primary key. Account failures compensate
only those recorded Account and automatic Character candidates, child first.

Existing typed rows reclass their canonical Python instance before the final
save. Account, Object, Channel, and Script keep canonical identity, update the
stored path and class together, avoid first-save hooks, and perform one
post-load refresh under the new class.

Relations remain model-specific. Account groups and permissions clear all
Django permission caches. Channel subscriptions use `SubscriptionHandler`.
Msg participants and hides use their owner-side relations. Tags, aliases, and
permissions use their live handlers rather than direct through-table writes.

## Honest outcomes and audit

A deterministic rejection before any write is a conflict. Once a write or
recorded primary key exists, later faults return non-retryable partial or
recovery-required outcomes after fresh durable verification and canonical
cache reconciliation. A pre-start bridge timeout is retryable; a callback that
started but outlived the worker wait is indeterminate and must not be retried.

Admin LogEntry rows are auxiliary worker state. Add/change/delete writes the
domain result first, then writes audit rows in a separate worker transaction.
Delete uses detached pre-delete display snapshots and logs only IDs proven
deleted. Audit failure cannot roll back or turn a completed mutation into a
retryable response; the administrator receives an explicit audit warning.

Single and bulk delete reload every target and recompute protected relations
and cascade permissions before lifecycle work. They call public instance
delete paths in deterministic order. JSONB lifecycle deletion remains in
autocommit and is never wrapped in an owner outer transaction.

## Account web flows

`evennia.web.utils.auth` owns credential verification, password rehash,
password change, reset-token mutation, admin and console usable/unusable
password modes, shared-login activation, `last_login`, and stock registration.

Authentication returns only an Account ID; Django reloads a detached Account
for session machinery. Password operations repeat mutable authorization and
validation on the owner, return a repr-hidden encoded password hash for
`update_session_auth_hash()`, and never save the detached user. The replacement
`user_logged_in` receiver is registered once for the exact AccountDB sender and
updates `last_login` through the owner. Its failure is auxiliary and logged
without undoing a completed session login.

Stock registration returns a frozen ID/name/issue result from provenance-aware
creation. It does not replace a game's registration, verification-mail, or
application workflow. Games overriding stock auth or registration routes must
adopt the same scalar services explicitly.

## Extension rule

Custom admins do not inherit this behavior automatically. Register a bounded
adapter or define a dedicated IO service. Standalone TagAdmin editing also
remains outside this bridge because one shared Tag row may be cached by many
owners; it needs a global invalidation design or a read-only policy.
