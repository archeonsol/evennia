"""Console API views.

The interesting part of this module is the failure mapping. A console
operation has five possible outcomes, not two, and collapsing them into
success-or-error is the easiest way to make the console worse than Django
admin at the thing Django admin is already bad at: telling an operator whether
the thing they just did actually happened.

============================  ======  ==========================================
Outcome                       Status  Meaning
============================  ======  ==========================================
success                       200     Completed and verified.
conflict                      409     Rejected before any write. Safe to retry.
timeout (pre-start)           504     Never started. Safe to retry.
indeterminate                 202     Started; outcome unknown. Do NOT retry.
unavailable (degraded)        503     IO owner is down. Reads still work.
============================  ======  ==========================================

Every response carries ``X-Console-Retryable`` so a client never has to infer
retry safety from a status code alone.

"""

from __future__ import annotations

from django.conf import settings
from django.http import Http404
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from evennia.console import health, spec
from evennia.console.registry import PanelError, dispatch, panel_registry
from evennia.web.console.auth import ConsolePermission, worker_context
from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    IOThreadCallUnavailable,
)


def _outcome(payload, *, code=status.HTTP_200_OK, retryable=False, outcome="success"):
    """Build one console response with explicit retry semantics.

    Args:
        payload: JSON-safe body.
        code: HTTP status code.
        retryable: Whether the caller may safely repeat the request.
        outcome: One of the five console outcomes.

    Returns:
        Response: The DRF response, with retry metadata in headers.
    """

    body = dict(payload) if isinstance(payload, dict) else {"result": payload}
    body.setdefault("outcome", outcome)
    response = Response(body, status=code)
    response["X-Console-Retryable"] = "true" if retryable else "false"
    response["X-Console-Outcome"] = outcome
    if code == status.HTTP_503_SERVICE_UNAVAILABLE:
        response["Retry-After"] = "1"
    return response


def _bridge_failure(error):
    """Map an IO-bridge failure onto a console response, or return ``None``.

    The distinction that matters: a call cancelled *before* it started never
    mutated anything and may be retried, while a call that started and outlived
    the worker's wait may have committed and must not be.
    """

    if isinstance(error, IOThreadCallIndeterminate):
        return _outcome(
            {
                "detail": (
                    "The operation started and its outcome is unknown. "
                    "Do not retry it; check the record before acting."
                )
            },
            code=status.HTTP_202_ACCEPTED,
            retryable=False,
            outcome="indeterminate",
        )
    if isinstance(error, IOThreadCallTimeout):
        return _outcome(
            {"detail": "The operation was cancelled before it started."},
            code=status.HTTP_504_GATEWAY_TIMEOUT,
            retryable=True,
            outcome="conflict",
        )
    if isinstance(error, IOThreadCallUnavailable):
        return _outcome(
            {
                "detail": (
                    "The game server is not reachable. Reads still work; "
                    "actions that touch game state are unavailable."
                ),
                "degraded": True,
            },
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
            outcome="unavailable",
        )
    return None


class ConsoleView(APIView):
    """Base view: console permission, no throttle, uniform failure mapping."""

    permission_classes = [ConsolePermission]
    # A leaked console session is bounded by the live capability re-check and
    # by session expiry, not by a rate limit; the console's own reads are
    # bounded by row caps in each panel.
    throttle_classes = []

    def handle_exception(self, exc):
        """Map bridge and panel failures before DRF's default handling."""

        mapped = _bridge_failure(exc)
        if mapped is not None:
            return mapped
        return super().handle_exception(exc)

    def _panel(self, key):
        """Return one registered panel the caller may reach."""

        try:
            return panel_registry.get(key)
        except PanelError as err:
            raise NotFound(str(err)) from err


class ConsoleAppView(TemplateView):
    """Serve the console shell.

    Deliberately not behind :class:`ConsolePermission`. The shell is a static
    document that renders nothing on its own; every byte of data it shows comes
    from the API, which does enforce the capability. Gating the page as well
    would mean an operator whose grant lapsed gets a bare 403 with no way to
    understand why, instead of the console telling them in its own words.
    """

    template_name = "console/index.html"

    def get(self, request, *args, **kwargs):
        """Return the shell, or 404 when the console is switched off."""
        if not getattr(settings, "CONSOLE_ENABLED", True):
            raise Http404("The console is disabled.")
        return super().get(request, *args, **kwargs)


class RootView(ConsoleView):
    """Nav, identity, and degraded state in one request."""

    def get(self, request):
        """Return the panels this caller may reach, plus server health."""
        ctx = worker_context(request)
        state = health.status()
        return _outcome(
            {
                "panels": [panel.describe() for panel in panel_registry.visible(ctx)],
                "actor": {"id": ctx.actor_id, "name": ctx.actor_name},
                "capabilities": sorted(ctx.capabilities),
                "degraded": state["degraded"],
                "version": state["version"],
                "settings": {
                    "repl_enabled": bool(getattr(settings, "CONSOLE_REPL_ENABLED", False)),
                    "sql_enabled": bool(getattr(settings, "CONSOLE_SQL_ENABLED", False)),
                    "server_control_enabled": bool(
                        getattr(settings, "CONSOLE_SERVER_CONTROL_ENABLED", False)
                    ),
                },
            }
        )


class HealthView(ConsoleView):
    """Machine-readable health, for the console and for uptime checks alike."""

    def get(self, request):
        """Return the health summary."""
        state = health.status()
        code = status.HTTP_200_OK if state["healthy"] else status.HTTP_503_SERVICE_UNAVAILABLE
        return _outcome(state, code=code, retryable=True, outcome="success")


class ModelSpecView(ConsoleView):
    """The model lens: every installed model, or one by label."""

    def get(self, request, label=None):
        """Return one model spec, or all of them in label order."""
        if label is None:
            return _outcome({"models": [item.as_dict() for item in spec.model_specs()]})
        try:
            return _outcome(spec.get_model_spec(label).as_dict())
        except LookupError as err:
            raise NotFound(str(err)) from err


class PanelRowsView(ConsoleView):
    """A panel's list view."""

    def get(self, request, key):
        """Return the panel's rows as plain data."""
        panel = self._panel(key)
        ctx = worker_context(request, **request.query_params.dict())
        if panel.needs_io and not health.io_available():
            return _degraded_panel(panel)
        try:
            return _outcome({"rows": dispatch(panel, "rows", ctx)})
        except PanelError as err:
            raise PermissionDenied(str(err)) from err


class PanelDetailView(ConsoleView):
    """A panel's detail view for one record."""

    def get(self, request, key, pk):
        """Return one record as plain data."""
        panel = self._panel(key)
        ctx = worker_context(request, **request.query_params.dict())
        if panel.needs_io and not health.io_available():
            return _degraded_panel(panel)
        try:
            return _outcome({"record": dispatch(panel, "detail", ctx, pk)})
        except PanelError as err:
            raise PermissionDenied(str(err)) from err


class PanelActionView(ConsoleView):
    """Invoke one named panel action."""

    def post(self, request, key, name):
        """Run the action and report its outcome honestly."""
        panel = self._panel(key)
        payload = request.data if isinstance(request.data, dict) else {}
        ctx = worker_context(request)
        if not health.io_available():
            return _degraded_panel(panel)
        try:
            result = dispatch(panel, name, ctx, **payload)
        except PanelError as err:
            raise PermissionDenied(str(err)) from err
        return _outcome({"result": result})


def _degraded_panel(panel):
    """Return the standard response for a panel that needs an absent IO owner."""

    return _outcome(
        {
            "detail": (
                "The game server is not reachable, so this action is disabled. "
                "Read-only panels remain available."
            ),
            "panel": panel.key,
            "degraded": True,
        },
        code=status.HTTP_503_SERVICE_UNAVAILABLE,
        retryable=True,
        outcome="unavailable",
    )
