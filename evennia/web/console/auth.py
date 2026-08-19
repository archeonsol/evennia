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

from django.conf import settings
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from evennia.authorization.service import has_capability
from evennia.console.registry import CONSOLE_ACCESS, CONSOLE_MODERATION, WorkerContext

#: Header a console request must carry alongside its session cookie.
CONSOLE_HEADER = "X-Evennia-Console"

#: Every capability that admits a caller to some part of the console.
CONSOLE_CAPABILITIES = (CONSOLE_ACCESS, CONSOLE_MODERATION)


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
        capability for capability in CONSOLE_CAPABILITIES if has_capability(user, capability)
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
