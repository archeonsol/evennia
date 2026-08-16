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


__all__ = (
    "AccountAlreadyExists",
    "AccountCreationIssue",
    "AccountCreationOutcome",
)
