"""Exact, opt-in provenance for Account and automatic Character creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MAX_ISSUE_TEXT = 1_000


class AccountAlreadyExists(ValueError):
    """An Account manager refused a case-insensitive duplicate username."""


@dataclass(frozen=True, slots=True)
class AccountCreationIssue:
    """One bounded, machine-readable Account creation issue."""

    stage: str
    code: str
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class AccountCreationOutcome:
    """IO-local result of an opt-in Account creation attempt.

    The model instances are deliberately retained for exact failure cleanup and
    must not cross a web-worker boundary. ``account_ids`` and ``object_ids``
    include instances assigned primary keys; callers must freshly verify whether
    each candidate row committed or rolled back.
    """

    disposition: str
    account: Any | None
    character: Any | None
    accounts: tuple[Any, ...]
    objects: tuple[Any, ...]
    account_ids: tuple[int, ...]
    object_ids: tuple[int, ...]
    issues: tuple[AccountCreationIssue, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccountCompensationOutcome:
    """Freshly verified result of exact child-first creation cleanup."""

    deleted_account_ids: tuple[int, ...]
    deleted_object_ids: tuple[int, ...]
    survivor_account_ids: tuple[int, ...]
    survivor_object_ids: tuple[int, ...]
    errors: tuple[str, ...]

    @property
    def survivor_ids(self):
        """Return the compatibility projection of all survivor identifiers."""
        return self.survivor_account_ids + self.survivor_object_ids


class _AccountCreationRecorder:
    """Collect exact model instances constructed by one opted-in attempt."""

    def __init__(self):
        """Initialize an empty, stack-local recorder."""
        self._accounts = []
        self._objects = []

    @staticmethod
    def _record_once(collection, instance):
        """Record one instance by Python identity without invoking model equality."""
        if instance is not None and all(existing is not instance for existing in collection):
            collection.append(instance)

    def record_account(self, account):
        """Record an Account instance before its first save."""
        self._record_once(self._accounts, account)

    def record_object(self, obj):
        """Record an Object instance before its first save."""
        self._record_once(self._objects, obj)

    def outcome(self, disposition, account, character, issues, errors):
        """Freeze the current recorder state into an Account creation outcome."""
        accounts = tuple(self._accounts)
        objects = tuple(self._objects)
        return AccountCreationOutcome(
            disposition=str(disposition),
            account=account,
            character=character,
            accounts=accounts,
            objects=objects,
            account_ids=tuple(int(item.pk) for item in accounts if item.pk is not None),
            object_ids=tuple(int(item.pk) for item in objects if item.pk is not None),
            issues=tuple(issues),
            errors=tuple(str(error) for error in errors),
        )


def account_creation_issue(stage, code, field, message):
    """Build one bounded structured issue for the opt-in creation outcome."""
    return AccountCreationIssue(
        stage=str(stage)[:64],
        code=str(code)[:64],
        field=str(field)[:32],
        message=str(message)[:_MAX_ISSUE_TEXT],
    )


def compensate_account_creation(outcome):
    """Delete only exact candidates from one provenance-aware creation attempt."""
    deleted_accounts = []
    deleted_objects = []
    errors = []

    for obj in reversed(outcome.objects):
        object_id = int(obj.pk) if obj.pk is not None else None
        if object_id is None:
            continue
        try:
            if obj.__class__.__dbclass__.objects.filter(pk=object_id).exists():
                obj.delete()
        except Exception as err:
            errors.append(f"object:{object_id}:{type(err).__name__}"[:_MAX_ISSUE_TEXT])
        if not obj.__class__.__dbclass__.objects.filter(pk=object_id).exists():
            deleted_objects.append(object_id)

    for account in reversed(outcome.accounts):
        account_id = int(account.pk) if account.pk is not None else None
        if account_id is None:
            continue
        try:
            if account.__class__.__dbclass__.objects.filter(pk=account_id).exists():
                account.delete()
        except Exception as err:
            errors.append(f"account:{account_id}:{type(err).__name__}"[:_MAX_ISSUE_TEXT])
        if not account.__class__.__dbclass__.objects.filter(pk=account_id).exists():
            deleted_accounts.append(account_id)

    account_survivors = (
        tuple(
            object_id
            for object_id in outcome.account_ids
            if outcome.accounts[0].__class__.__dbclass__.objects.filter(pk=object_id).exists()
        )
        if outcome.accounts
        else ()
    )
    object_survivors = (
        tuple(
            object_id
            for object_id in outcome.object_ids
            if outcome.objects[0].__class__.__dbclass__.objects.filter(pk=object_id).exists()
        )
        if outcome.objects
        else ()
    )
    return AccountCompensationOutcome(
        deleted_account_ids=tuple(deleted_accounts),
        deleted_object_ids=tuple(deleted_objects),
        survivor_account_ids=account_survivors,
        survivor_object_ids=object_survivors,
        errors=tuple(errors),
    )


__all__ = (
    "AccountAlreadyExists",
    "AccountCompensationOutcome",
    "AccountCreationIssue",
    "AccountCreationOutcome",
    "compensate_account_creation",
)
