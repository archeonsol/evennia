"""Authentication and authorization for the console API.

Two rules, both consequences of the console being a single capability.

**Capability is re-checked live, per request.** Never cached at login, never
trusted from a token. A demotion, a suspension, or a ``@quell`` revokes console
access on the next request rather than at the next session.

**Session integrity is the whole defence.** With no internal permission
boundaries, a stolen console session is total compromise, so the checks that
would be defence in depth elsewhere are the defence here. The session cookie
authenticates and a scoped custom header must accompany it: a custom header is
unreadable cross-origin and preflight-gated, so it is also the CSRF defence,
and a console URL forwarded to somebody else authorizes nobody.

"""

from __future__ import annotations

import time

from django.conf import settings
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from evennia.authorization.service import has_capability
from evennia.console.registry import (
    CONSOLE_ACCESS,
    CONSOLE_MODERATION,
    CONSOLE_MODERATION_ADDRESS,
    CONSOLE_MODERATION_PERMANENT,
    WorkerContext,
)

#: Header a console request must carry alongside its session cookie.
CONSOLE_HEADER = "X-Evennia-Console"

#: Every capability that admits a caller to some part of the console.
CONSOLE_CAPABILITIES = (CONSOLE_ACCESS, CONSOLE_MODERATION)

#: Capabilities that grant authority *inside* a panel rather than admission to
#: one. Holding one of these alone admits nobody: it only widens what a caller
#: already admitted may do.
CONSOLE_AUTHORITIES = (CONSOLE_MODERATION_ADDRESS, CONSOLE_MODERATION_PERMANENT)

#: Everything resolved onto a request context, admitting and authorising alike.
RESOLVED_CAPABILITIES = CONSOLE_CAPABILITIES + CONSOLE_AUTHORITIES

#: Session keys holding console-scoped timers.
IDLE_KEY = "_console_seen"
REAUTH_KEY = "_console_reauth"


class ConsoleIdle(PermissionDenied):
    """The console session sat idle past its own bound."""


class ConsoleInsecure(PermissionDenied):
    """The console was reached over a connection that cannot carry a shell."""


def _now():
    """Return a monotonic-enough wall clock for session timers."""

    return int(time.time())


def idle_timeout():
    """Return the console's idle bound in seconds."""

    return int(getattr(settings, "CONSOLE_IDLE_TIMEOUT", 1800) or 0)


def enforce_transport(request):
    """Refuse to serve a shell credential over a plain connection.

    ``SESSION_COOKIE_SECURE`` is a site-wide player-website default and is
    routinely off. That is defensible for a forum session and not for one that
    carries a REPL, so the console checks the transport itself.

    Raises:
        ConsoleInsecure: The request is not secure and insecure use was not
            explicitly permitted.
    """

    if request.is_secure() or getattr(settings, "CONSOLE_ALLOW_INSECURE", False):
        return
    raise ConsoleInsecure(
        "The console refuses a non-TLS connection. Set CONSOLE_ALLOW_INSECURE "
        "for localhost development."
    )


def touch_idle(request):
    """Record console activity, refusing a session that sat idle too long.

    Refused rather than expired: the player's own session is left alone, so
    somebody whose console lapsed is still signed in to the website and is told
    what happened rather than silently logged out of everything.

    Raises:
        ConsoleIdle: The session exceeded the console's idle bound.
    """

    bound = idle_timeout()
    if bound <= 0:
        return
    session = getattr(request, "session", None)
    if session is None:
        return
    now = _now()
    seen = session.get(IDLE_KEY)
    if seen is not None and now - int(seen) > bound:
        session.pop(IDLE_KEY, None)
        session.pop(REAUTH_KEY, None)
        raise ConsoleIdle(
            f"This console session sat idle for more than {bound} seconds. "
            "Sign in again to continue."
        )
    session[IDLE_KEY] = now


def mark_reauthenticated(request):
    """Record that the operator just proved they are present."""

    request.session[REAUTH_KEY] = _now()


def reauthenticated(request):
    """Return whether a recent password re-entry still stands."""

    window = int(getattr(settings, "CONSOLE_REAUTH_WINDOW", 300) or 0)
    stamp = getattr(request, "session", {}).get(REAUTH_KEY)
    if stamp is None:
        return False
    return _now() - int(stamp) <= window


def require_reauthentication(request):
    """Demand proof of presence before a dangerous action.

    A capability says who you are. This says you are *here* -- which is the
    only thing standing between an unlocked laptop and a REPL.

    Raises:
        PermissionDenied: No recent re-entry stands.
    """

    if not reauthenticated(request):
        raise PermissionDenied(
            "Confirm your password to run this. The confirmation lasts "
            f"{int(getattr(settings, 'CONSOLE_REAUTH_WINDOW', 300))} seconds."
        )


def _meta_key(header: str) -> str:
    """Return the WSGI META key for one HTTP header name."""

    return "HTTP_" + header.upper().replace("-", "_")


def live_capabilities(user) -> frozenset[str]:
    """Return the console capabilities one user holds right now.

    Resolved against the authorization runtime on every call. The result is
    request-scoped and must not be cached across requests.

    Args:
        user: The authenticated account.

    Returns:
        frozenset[str]: Held console capabilities, possibly empty.
    """

    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    return frozenset(
        capability for capability in RESOLVED_CAPABILITIES if has_capability(user, capability)
    )


class ConsolePermission(permissions.BasePermission):
    """Admit a live holder of any console capability, carrying the header."""

    message = "The console requires a current console capability."

    def has_permission(self, request, view):
        """Return whether this request may reach the console API."""

        if not getattr(settings, "CONSOLE_ENABLED", True):
            return False
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if not request.META.get(_meta_key(CONSOLE_HEADER)):
            raise PermissionDenied("Console requests must carry the console header.")
        enforce_transport(request)
        touch_idle(request)
        capabilities = live_capabilities(user)
        if not capabilities:
            return False
        # Stash for the view; recomputing per view would double the cost of an
        # already cache-backed lookup, but it must never outlive the request.
        request._console_capabilities = capabilities
        return True


def worker_context(request, **params) -> WorkerContext:
    """Build the worker-side context for one console request.

    Args:
        request: The DRF request.
        **params: Bounded request parameters to carry into the panel.

    Returns:
        WorkerContext: Scalars only, with no handle on live game state.
    """

    user = request.user
    capabilities = getattr(request, "_console_capabilities", None)
    if capabilities is None:
        capabilities = live_capabilities(user)
    return WorkerContext(
        actor_id=int(getattr(user, "pk", 0) or 0),
        actor_name=str(getattr(user, "username", ""))[:255],
        capabilities=capabilities,
        params=params,
    )
