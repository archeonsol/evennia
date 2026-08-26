"""IO-owner authentication, password, and registration services for Django."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.apps import apps
from django.contrib.auth.hashers import check_password as check_password_hash
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from evennia.accounts.creation import compensate_account_creation
from evennia.utils import class_from_module, logger
from evennia.web.utils.io import release_worker_db_connections, run_on_io_thread


@dataclass(frozen=True, slots=True)
class AuthenticationRequest:
    """Secret-bearing request for owner-side authentication."""

    username: str | None = None
    password: str | None = field(default=None, repr=False)
    autologin_id: int | None = None


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """Plain authentication result."""

    status: str
    account_id: int | None = None
    needs_rehash: bool = False


@dataclass(frozen=True, slots=True)
class PasswordMutationRequest:
    """Secret-bearing password mutation request."""

    account_id: int
    mode: str
    new_password: str | None = field(default=None, repr=False)
    old_password: str | None = field(default=None, repr=False)
    reset_token: str | None = field(default=None, repr=False)
    actor_id: int | None = None


@dataclass(frozen=True, slots=True)
class PasswordMutationResult:
    """Plain password mutation result with a repr-hidden session hash source."""

    status: str
    account_id: int
    password_hash: str = field(default="", repr=False)
    message: str = ""


@dataclass(frozen=True, slots=True)
class RegistrationRequest:
    """Bounded stock registration input."""

    username: str
    email: str
    password: str = field(repr=False)
    ip: str = ""
    typeclass_path: str = ""


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """Plain stock registration outcome."""

    status: str
    account_id: int | None = None
    account_name: str = ""
    issues: tuple[tuple[str, str, str], ...] = ()
    recovery_ids: tuple[int, ...] = ()


def check_credentials(request: AuthenticationRequest) -> AuthenticationResult:
    """Verify credentials without the IO owner.

    Authentication needs three plain columns and a hash comparison. It touches
    no game object, no attribute, and no handler, so nothing about it requires
    owner-scoped identity -- the only reason the owner was involved is that
    calling ``account.check_password`` first *materializes* an ``AccountDB``
    instance, and instance identity is owner-scoped.

    Reading through ``values()`` constructs no instance at all, and
    ``django.contrib.auth.hashers.check_password`` compares two strings. Same
    lookup, same active check, same hasher, same verdict.

    This is what makes degraded mode real. The console keeps serving reads when
    the game server is down, which is worthless if nobody can sign in to reach
    them, and an operator arriving at an outage is exactly the person who
    needs to.

    Args:
        request: The credentials to check.

    Returns:
        AuthenticationResult: ``authenticated`` with an account id, or
        ``rejected``. ``needs_rehash`` marks a valid password stored under a
        superseded hasher.
    """

    AccountDB = apps.get_model("accounts", "AccountDB")
    if request.autologin_id is not None:
        row = (
            AccountDB.objects.filter(pk=int(request.autologin_id)).values("id", "is_active").first()
        )
        if not row or not row["is_active"]:
            return AuthenticationResult("rejected")
        return AuthenticationResult("authenticated", int(row["id"]))

    if not request.username or request.password is None:
        return AuthenticationResult("rejected")
    row = (
        AccountDB.objects.filter(username__iexact=request.username)
        .values("id", "password", "is_active")
        .first()
    )
    if not row or not row["is_active"]:
        return AuthenticationResult("rejected")

    stale = False

    def _mark_stale(_raw_password):
        """Record that the stored hash uses a superseded hasher."""
        nonlocal stale
        stale = True

    if not check_password_hash(request.password, row["password"], setter=_mark_stale):
        return AuthenticationResult("rejected")
    return AuthenticationResult("authenticated", int(row["id"]), needs_rehash=stale)


def rehash_password(account_id: int, raw_password: str) -> bool:
    """Upgrade one stored password to the current hasher, on the owner.

    Deliberately separate from the credential check, and deliberately
    optional. A valid password under a superseded hasher is a missed
    optimization, not a security failure, so this must never be the reason
    somebody cannot sign in.

    Args:
        account_id: Account whose hash should be upgraded.
        raw_password: The password just verified.

    Returns:
        bool: Whether the hash was rewritten.
    """

    AccountDB = apps.get_model("accounts", "AccountDB")
    try:
        account = AccountDB.objects.get(pk=int(account_id))
    except AccountDB.DoesNotExist:
        return False
    if not account.is_active or not account.check_password(raw_password):
        return False
    account.set_password(raw_password)
    account.save(update_fields=["password"])
    return True


def authenticate_account(request: AuthenticationRequest) -> AuthenticationResult:
    """Check credentials and optional password rehash entirely on the owner.

    Retained as the owner-side path. :func:`check_credentials` is the one the
    login backend uses; both must always agree on accept and reject.
    """
    AccountDB = apps.get_model("accounts", "AccountDB")
    if request.autologin_id is not None:
        account = AccountDB.objects.filter(pk=int(request.autologin_id)).first()
        if account is None or not account.is_active:
            return AuthenticationResult("rejected")
        return AuthenticationResult("authenticated", int(account.pk))
    if not request.username or request.password is None:
        return AuthenticationResult("rejected")
    account = AccountDB.objects.filter(username__iexact=request.username).first()
    if account is None or not account.is_active or not account.check_password(request.password):
        return AuthenticationResult("rejected")
    return AuthenticationResult("authenticated", int(account.pk))


def _fresh_admin(actor_id, target):
    """Recompute Account-change permission from fresh owner state."""
    AccountDB = apps.get_model("accounts", "AccountDB")
    fresh = (
        AccountDB.objects.filter(pk=int(actor_id))
        .values("is_active", "is_staff", "is_superuser")
        .first()
    )
    if not fresh or not fresh["is_active"] or not fresh["is_staff"]:
        raise PermissionDenied("Admin access was revoked")
    actor = AccountDB.objects.get(pk=int(actor_id))
    actor.is_active = fresh["is_active"]
    actor.is_staff = fresh["is_staff"]
    actor.is_superuser = fresh["is_superuser"]
    for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        actor.__dict__.pop(cache_name, None)
    codename = f"{target._meta.app_label}.change_{target._meta.model_name}"
    if not actor.has_perm(codename):
        raise PermissionDenied("Admin permission was revoked")


def mutate_password(request: PasswordMutationRequest) -> PasswordMutationResult:
    """Repeat mutable authorization and update one password on the owner."""
    AccountDB = apps.get_model("accounts", "AccountDB")
    try:
        account = AccountDB.objects.get(pk=int(request.account_id))
    except AccountDB.DoesNotExist:
        return PasswordMutationResult("missing", int(request.account_id), message="Account missing")

    if request.mode == "change":
        if not account.is_active or not account.check_password(request.old_password):
            return PasswordMutationResult(
                "rejected",
                int(account.pk),
                message="The old password is no longer valid.",
            )
    elif request.mode == "reset":
        if not account.is_active or not default_token_generator.check_token(
            account, request.reset_token
        ):
            return PasswordMutationResult(
                "rejected",
                int(account.pk),
                message="The reset token is no longer valid.",
            )
    elif request.mode in ("admin_set", "admin_unusable"):
        if request.actor_id is None:
            raise PermissionDenied("Admin identity is required")
        _fresh_admin(request.actor_id, account)
    elif request.mode in ("console_set", "console_unusable"):
        if request.actor_id is None:
            raise PermissionDenied("Console identity is required")
        actor = AccountDB.objects.filter(pk=int(request.actor_id)).values("is_active").first()
        if not actor or not actor["is_active"]:
            raise PermissionDenied("Console access was revoked")
    else:
        raise ValueError("Unknown password operation")

    if request.mode in ("admin_unusable", "console_unusable"):
        account.set_unusable_password()
    else:
        validator = getattr(account, "validate_password", None)
        if callable(validator):
            valid, error = validator(request.new_password, account=account)
        else:
            # A raw AccountDB can exist during migrations, tests, imports, and
            # repair work. It has Django's password methods but none of the
            # typeclass helpers, so use the same validator stack directly.
            from django.contrib.auth import password_validation

            try:
                password_validation.validate_password(request.new_password, user=account)
                valid, error = True, None
            except ValidationError as err:
                valid, error = False, err
        if not valid:
            message = ", ".join(getattr(error, "messages", (str(error),)))
            return PasswordMutationResult("rejected", int(account.pk), message=message[:500])
        account.set_password(request.new_password)
    account.save(update_fields=["password"])
    return PasswordMutationResult("changed", int(account.pk), password_hash=str(account.password))


def update_last_login_owner(account_id: int, timestamp=None) -> bool:
    """Update ``last_login`` on the canonical owner instance."""
    AccountDB = apps.get_model("accounts", "AccountDB")
    account = AccountDB.objects.filter(pk=int(account_id)).first()
    if account is None:
        return False
    account.last_login = timestamp or timezone.now()
    account.save(update_fields=["last_login"])
    return True


def owner_update_last_login(sender, user, **kwargs):
    """Django login receiver whose auxiliary failure cannot undo a session."""
    try:
        release_worker_db_connections()
        run_on_io_thread(update_last_login_owner, int(user.pk), timezone.now())
    except Exception as err:
        logger.log_warn(
            f"Owner last_login update failed for Account #{getattr(user, 'pk', None)}: "
            f"{type(err).__name__}"
        )


def register_account(request: RegistrationRequest) -> RegistrationResult:
    """Create a stock website Account with exact failure provenance."""
    typeclass_path = request.typeclass_path
    if not typeclass_path:
        from django.conf import settings

        typeclass_path = settings.BASE_ACCOUNT_TYPECLASS
    Account = class_from_module(typeclass_path)
    outcome = Account.create_with_provenance(
        username=request.username,
        email=request.email,
        password=request.password,
        ip=request.ip,
        typeclass=typeclass_path,
    )
    if outcome.disposition == "created" and outcome.account is not None:
        return RegistrationResult(
            "created", int(outcome.account.pk), str(outcome.account.username)[:150]
        )
    cleanup = compensate_account_creation(outcome)
    issues = tuple(
        (str(issue.field)[:32], str(issue.code)[:64], str(issue.message)[:500])
        for issue in outcome.issues[:32]
    )
    return RegistrationResult(
        (
            "recovery_required"
            if cleanup.survivor_ids
            else ("rejected" if outcome.disposition == "rejected" else "fault")
        ),
        issues=issues,
        recovery_ids=cleanup.survivor_ids,
    )


__all__ = (
    "AuthenticationRequest",
    "AuthenticationResult",
    "PasswordMutationRequest",
    "PasswordMutationResult",
    "RegistrationRequest",
    "RegistrationResult",
    "authenticate_account",
    "mutate_password",
    "owner_update_last_login",
    "register_account",
    "update_last_login_owner",
)
