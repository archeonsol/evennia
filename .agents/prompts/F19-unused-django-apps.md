# F19: unused Django apps in `INSTALLED_APPS`

Status: todo

## Goal

Two Django contrib apps in `INSTALLED_APPS` aren't used:

- `django.contrib.flatpages` — zero usage.
- `django.contrib.admindocs` — one `/doc/` admin URL, likely
  never hit.

Remove both, with fake-apply migration discipline so existing
deployments don't break.

## Approach

This is mechanical work with one risky bit (migrations).

1. Confirm zero usage. `grep` for `flatpages` and `admindocs`
   across the engine and any consumer the user names.
2. Remove from `INSTALLED_APPS` in
   `evennia/settings_default.py`.
3. Remove their URL patterns from `evennia/web/urls.py` (or
   wherever URL routing lives).
4. **Migration discipline**: each removed app's tables will
   still exist in deployed databases. Use the fake-apply
   pattern — generate a no-op migration in the engine that
   tells Django "these tables exist but we no longer manage
   them," so a deployment upgrading from the current version
   doesn't try to drop tables or fail on missing migration
   history.
5. Test the upgrade path on a stock test database that has
   these apps installed; confirm reload works.

## Background

Django ships these in `INSTALLED_APPS` defaults; carrying them
into Evennia made sense before the engine had a clear stance
on what it ships. Both are unused; flatpages is dead weight,
admindocs is one URL that points at auto-generated docs of code
nobody reads.

## Scope boundary

- **In scope**: the two named apps.
- **Out of scope**: broader audit of `INSTALLED_APPS`; URL
  routing reorg; admin UI changes beyond removing the
  affected `/doc/` URL.

## Existing code to study

- `evennia/settings_default.py` — `INSTALLED_APPS` definition.
- `evennia/web/urls.py` — URL routing.
- Django docs on `migrations.SeparateDatabaseAndState` or
  similar for the fake-apply pattern.
- Any past PR in the repo that removed a Django app — useful
  precedent for migration handling.

## Done means

- Apps removed from `INSTALLED_APPS`.
- URL patterns removed.
- Fake-apply migration present.
- Existing test suite passes; upgrade-from-current path
  verified.
- Release note: downstream operators should not see their
  database touched.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Skipping the fake-apply migration (deployments will break).
- Removing additional Django apps in the same PR (separate
  decisions).
- Touching the admin UI beyond what removal requires.
