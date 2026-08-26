"""Tests for the console API.

Three things carry the weight here.

*Access* is one live capability plus a header. There are no internal
permission boundaries to test except the moderation-only one, so that one gets
tested by enumerating every route rather than by example.

*Outcomes* are five, not two. A client must be able to tell "never started,
retry" from "started, unknown, do not retry" without parsing prose.

*Degraded mode* is the capability Django admin does not have, and it is easy to
break and hard to notice: it works the day it ships and rots the first time a
panel adds an unconditional IO call.

"""

import time
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from evennia.console.registry import (
    CONSOLE_ACCESS,
    CONSOLE_MODERATION,
    Panel,
    io_action,
    panel_registry,
)
from evennia.web.console import auth as console_auth
from evennia.web.console import views as console_views
from evennia.web.utils.io import (
    IOThreadCallIndeterminate,
    IOThreadCallTimeout,
    IOThreadCallUnavailable,
)

HEADERS = {"HTTP_X_EVENNIA_CONSOLE": "1", "secure": True}


class PlainPanel(Panel):
    """Reads plain models only, so it survives degraded mode."""

    key = "plain"
    label = "Plain"
    needs_io = False

    def rows(self, ctx):
        return [{"id": 1, "name": "row"}]

    def detail(self, ctx, pk):
        return {"id": pk}

    def lookup(self, ctx, term=""):
        """A worker-side action. Reads the database, never the IO owner."""
        return {"term": term}

    def invalid(self, ctx):
        """Reject operator input with the panel's normal validation error."""
        raise ValueError("Enter a supported value.")

    def conflict_result(self, ctx):
        """Return a service rejection after a completed bridge call."""
        return {"status": "conflict", "message": "The row changed elsewhere."}

    def partial_result(self, ctx):
        """Return a service result that requires operator recovery."""
        return {"status": "recovery_required", "message": "Inspect row 7 before retrying."}

    @io_action
    def touch(self, ctx):
        """An action that needs the IO owner."""
        return {"touched": True}


class LivePanel(Panel):
    """Needs the IO owner, so it must disable itself when it is gone."""

    key = "live"
    label = "Live"
    needs_io = True

    def rows(self, ctx):
        return [{"id": 2}]


class ModerationOnlyPanel(Panel):
    """Reachable by the moderation capability."""

    key = "modonly"
    label = "Moderation"
    moderation_only = True

    def rows(self, ctx):
        return []


class ConsoleAPITestCase(TestCase):
    """Shared setup: a registered panel set and a signed-in account."""

    capabilities = frozenset({CONSOLE_ACCESS})

    def setUp(self):
        from django.contrib.auth import get_user_model

        panel_registry._reset_for_tests()
        for panel in (PlainPanel, LivePanel, ModerationOnlyPanel):
            panel_registry.register(panel)
        self.account = get_user_model().objects.create(username="console-tester")
        self.client.force_login(self.account)
        patcher = patch.object(
            console_auth, "live_capabilities", side_effect=lambda user: self.capabilities
        )
        self.addCleanup(patcher.stop)
        patcher.start()
        io_patcher = patch.object(console_views.health, "io_available", return_value=True)
        self.addCleanup(io_patcher.stop)
        self.io_available = io_patcher.start()

    def tearDown(self):
        panel_registry._reset_for_tests()


class TestAccess(ConsoleAPITestCase):
    """One capability admits; the header is required; checks are live."""

    def test_root_returns_the_nav(self):
        response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(response.status_code, 200)
        keys = [panel["key"] for panel in response.json()["panels"]]
        self.assertEqual(keys, ["live", "modonly", "plain"])

    def test_missing_header_is_refused(self):
        response = self.client.get(reverse("console:root"))
        self.assertEqual(response.status_code, 403)

    def test_no_capability_is_refused(self):
        self.capabilities = frozenset()
        response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_refused(self):
        self.client.logout()
        response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertIn(response.status_code, (401, 403))

    @override_settings(CONSOLE_ENABLED=False)
    def test_disabled_console_refuses_everything(self):
        response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(response.status_code, 403)

    def test_capability_is_rechecked_per_request(self):
        first = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(first.status_code, 200)
        # A demotion between requests takes effect on the next one, with no
        # session invalidation and no cache expiry to wait for.
        self.capabilities = frozenset()
        second = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(second.status_code, 403)

    def test_root_reports_the_dangerous_settings(self):
        payload = self.client.get(reverse("console:root"), **HEADERS).json()
        self.assertFalse(payload["settings"]["repl_enabled"])
        self.assertFalse(payload["settings"]["sql_enabled"])
        self.assertFalse(payload["settings"]["server_control_enabled"])


class TestModerationBoundary(ConsoleAPITestCase):
    """The one internal boundary, tested by enumeration."""

    capabilities = frozenset({CONSOLE_MODERATION})

    def test_nav_shows_only_the_moderation_panel(self):
        payload = self.client.get(reverse("console:root"), **HEADERS).json()
        self.assertEqual([panel["key"] for panel in payload["panels"]], ["modonly"])

    def test_moderation_panel_is_reachable(self):
        response = self.client.get(reverse("console:panel-rows", args=["modonly"]), **HEADERS)
        self.assertEqual(response.status_code, 200)

    def test_every_other_panel_route_is_unreachable(self):
        # Enumerated rather than sampled: a newly added panel must fail this
        # test until somebody has deliberately considered it.
        for key in ("plain", "live"):
            for url in (
                reverse("console:panel-rows", args=[key]),
                reverse("console:panel-detail", args=[key, "1"]),
            ):
                self.assertEqual(
                    self.client.get(url, **HEADERS).status_code, 403, f"{url} was reachable"
                )
            self.assertEqual(
                self.client.post(
                    reverse("console:panel-action", args=[key, "rows"]), **HEADERS
                ).status_code,
                403,
            )


class TestPanelRoutes(ConsoleAPITestCase):
    """Rows, detail, and unknown panels."""

    def test_rows(self):
        response = self.client.get(reverse("console:panel-rows", args=["plain"]), **HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], [{"id": 1, "name": "row"}])

    def test_detail(self):
        response = self.client.get(reverse("console:panel-detail", args=["plain", "9"]), **HEADERS)
        self.assertEqual(response.json()["record"], {"id": "9"})

    def test_unknown_panel_is_a_404(self):
        response = self.client.get(reverse("console:panel-rows", args=["nope"]), **HEADERS)
        self.assertEqual(response.status_code, 404)

    def test_private_method_is_not_an_action(self):
        response = self.client.post(
            reverse("console:panel-action", args=["plain", "_secret"]), **HEADERS
        )
        self.assertIn(response.status_code, (403, 404))


class TestOutcomeMapping(ConsoleAPITestCase):
    """Five outcomes, each distinguishable without reading prose."""

    def _post(self):
        return self.client.post(reverse("console:panel-action", args=["plain", "rows"]), **HEADERS)

    def test_success_is_retryable_false_and_labelled(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Console-Outcome"], "success")

    def test_indeterminate_is_202_and_must_not_be_retried(self):
        with patch.object(console_views, "dispatch", side_effect=IOThreadCallIndeterminate("x")):
            response = self._post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response["X-Console-Retryable"], "false")
        self.assertEqual(response["X-Console-Outcome"], "indeterminate")

    def test_pre_start_timeout_is_retryable(self):
        with patch.object(console_views, "dispatch", side_effect=IOThreadCallTimeout("x")):
            response = self._post()
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response["X-Console-Retryable"], "true")

    def test_unavailable_is_503_with_retry_after(self):
        with patch.object(console_views, "dispatch", side_effect=IOThreadCallUnavailable("x")):
            response = self._post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(response["X-Console-Outcome"], "unavailable")

    def test_indeterminate_never_claims_retryable(self):
        # The single most consequential header in the API: an automatic retry
        # of a mutation that may have committed is how duplicates happen.
        with patch.object(console_views, "dispatch", side_effect=IOThreadCallIndeterminate("x")):
            response = self._post()
        self.assertNotEqual(response["X-Console-Retryable"], "true")

    def test_panel_validation_is_a_bounded_bad_request(self):
        response = self.client.post(
            reverse("console:panel-action", args=["plain", "invalid"]), **HEADERS
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["X-Console-Outcome"], "conflict")
        self.assertEqual(response["X-Console-Retryable"], "true")
        self.assertIn("supported value", response.json()["detail"])

    def test_service_conflict_is_not_labeled_as_success(self):
        response = self.client.post(
            reverse("console:panel-action", args=["plain", "conflict_result"]), **HEADERS
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response["X-Console-Outcome"], "conflict")
        self.assertEqual(response["X-Console-Retryable"], "true")

    def test_partial_service_result_is_indeterminate_and_not_retryable(self):
        response = self.client.post(
            reverse("console:panel-action", args=["plain", "partial_result"]), **HEADERS
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response["X-Console-Outcome"], "indeterminate")
        self.assertEqual(response["X-Console-Retryable"], "false")


class TestDegradedMode(ConsoleAPITestCase):
    """Game server down, web server up: reads survive, actions disable."""

    def setUp(self):
        super().setUp()
        self.io_available.return_value = False

    def test_root_still_answers_and_reports_degraded(self):
        response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["degraded"])

    def test_plain_panel_still_reads(self):
        response = self.client.get(reverse("console:panel-rows", args=["plain"]), **HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], [{"id": 1, "name": "row"}])

    def test_model_lens_still_reads(self):
        response = self.client.get(reverse("console:models"), **HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["models"])

    def test_io_dependent_panel_disables_itself_with_a_reason(self):
        response = self.client.get(reverse("console:panel-rows", args=["live"]), **HEADERS)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["degraded"])
        self.assertIn("not reachable", response.json()["detail"])

    def test_an_action_that_needs_the_io_owner_is_disabled(self):
        response = self.client.post(
            reverse("console:panel-action", args=["plain", "touch"]), **HEADERS
        )
        self.assertEqual(response.status_code, 503)

    def test_a_worker_side_action_still_runs(self):
        # Gated on the action, not on the panel. A worker-side action reads the
        # database and never touches the IO owner, so refusing it during an
        # outage removes a read that still works -- the opposite of what
        # degraded mode is for.
        response = self.client.post(
            reverse("console:panel-action", args=["plain", "lookup"]),
            data={"term": "x"},
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {"term": "x"})

    def test_every_registered_panel_answers_rather_than_500s(self):
        # The regression net for degraded mode: a panel that adds an
        # unconditional IO call fails here instead of in production.
        for panel in panel_registry.panels():
            if panel.moderation_only:
                continue
            code = self.client.get(
                reverse("console:panel-rows", args=[panel.key]), **HEADERS
            ).status_code
            self.assertIn(code, (200, 503), f"{panel.key} returned {code} in degraded mode")


class TestModelLens(ConsoleAPITestCase):
    """The generic lens covers models Django admin never registered."""

    def test_lists_every_installed_model(self):
        payload = self.client.get(reverse("console:models"), **HEADERS).json()
        labels = {item["label"] for item in payload["models"]}
        for label in ("server.sanction", "server.moderationflag", "server.enginejob"):
            self.assertIn(label, labels)

    def test_one_model_by_label(self):
        payload = self.client.get(
            reverse("console:model-detail", args=["server.sanction"]), **HEADERS
        ).json()
        self.assertEqual(payload["label"], "server.sanction")
        self.assertFalse(payload["writable"])
        self.assertIn("hash chain", payload["write_via"])

    def test_unknown_label_is_a_404(self):
        response = self.client.get(
            reverse("console:model-detail", args=["nope.nothing"]), **HEADERS
        )
        self.assertEqual(response.status_code, 404)


class TestHealthEndpoint(ConsoleAPITestCase):
    """Health is served as plain JSON for uptime checks too."""

    def test_healthy(self):
        response = self.client.get(reverse("console:health"), **HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["checks"]["database"])

    def test_unhealthy_when_the_io_owner_is_gone(self):
        self.io_available.return_value = False
        response = self.client.get(reverse("console:health"), **HEADERS)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["degraded"])


class TestInputErrors(ConsoleAPITestCase):
    """A fixable mistake gets a sentence, not a 500.

    Panels reject bad input by raising, and those exceptions carry the only
    explanation an operator will see: which field does not exist, which
    comparison is unsupported.
    """

    def setUp(self):
        super().setUp()
        panel_registry._reset_for_tests()
        from evennia.console.panels import register_builtin_panels

        register_builtin_panels(panel_registry)

    def test_unknown_filter_field_is_a_400_naming_the_field(self):
        response = self.client.get(
            reverse("console:panel-rows", args=["records"])
            + "?model=console.consoleauditevent&f.nope=1",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nope", str(response.json()))

    def test_unsupported_comparison_is_a_400(self):
        response = self.client.get(
            reverse("console:panel-rows", args=["records"])
            + "?model=console.consoleauditevent&f.panel__regex=x",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_sort_field_is_a_400(self):
        response = self.client.get(
            reverse("console:panel-rows", args=["records"])
            + "?model=console.consoleauditevent&order=nope",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_model_is_a_404_naming_the_label(self):
        response = self.client.get(
            reverse("console:panel-rows", args=["records"]) + "?model=nope.nothing",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("nope.nothing", str(response.json()))

    def test_missing_row_is_a_404(self):
        response = self.client.get(
            reverse("console:panel-detail", args=["records", "999999"])
            + "?model=console.consoleauditevent",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 404)

    def test_no_input_error_becomes_a_500(self):
        # The regression this guards: an uncaught FieldError reaching the
        # default handler tells the operator nothing at all.
        for suffix in ("&f.nope=1", "&order=nope", "&f.panel__regex=x"):
            response = self.client.get(
                reverse("console:panel-rows", args=["records"])
                + "?model=console.consoleauditevent"
                + suffix,
                **HEADERS,
            )
            self.assertLess(response.status_code, 500, f"{suffix} produced a server error")


class TestTransport(ConsoleAPITestCase):
    """A shell credential does not travel over a plain connection."""

    def test_plain_http_is_refused(self):
        # SESSION_COOKIE_SECURE is a site-wide player-website default and is
        # routinely off. The console checks the transport itself rather than
        # inheriting a setting chosen for forum sessions.
        response = self.client.get(reverse("console:root"), HTTP_X_EVENNIA_CONSOLE="1")
        self.assertEqual(response.status_code, 403)
        self.assertIn("TLS", response.json()["detail"])

    @override_settings(CONSOLE_ALLOW_INSECURE=True)
    def test_localhost_development_can_opt_out(self):
        response = self.client.get(reverse("console:root"), HTTP_X_EVENNIA_CONSOLE="1")
        self.assertEqual(response.status_code, 200)


class TestIdleBound(ConsoleAPITestCase):
    """A console session is a shell, so it does not sit open for a fortnight."""

    @override_settings(CONSOLE_IDLE_TIMEOUT=1)
    def test_an_idle_session_is_refused(self):
        self.assertEqual(self.client.get(reverse("console:root"), **HEADERS).status_code, 200)
        session = self.client.session
        session["_console_seen"] = int(time.time()) - 600
        session.save()
        response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(response.status_code, 403)
        self.assertIn("idle", response.json()["detail"].lower())

    @override_settings(CONSOLE_IDLE_TIMEOUT=1)
    def test_the_refusal_tells_the_operator_what_to_do(self):
        session = self.client.session
        session["_console_seen"] = int(time.time()) - 600
        session.save()
        payload = self.client.get(reverse("console:root"), **HEADERS).json()
        self.assertTrue(payload["reauthenticate"])

    @override_settings(CONSOLE_IDLE_TIMEOUT=1)
    def test_the_website_session_survives(self):
        # Refused, not expired: somebody whose console lapsed is still signed
        # in to the site and is told what happened.
        session = self.client.session
        session["_console_seen"] = int(time.time()) - 600
        session.save()
        self.client.get(reverse("console:root"), **HEADERS)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    @override_settings(CONSOLE_IDLE_TIMEOUT=3600)
    def test_activity_keeps_a_session_alive(self):
        for _ in range(3):
            self.assertEqual(self.client.get(reverse("console:root"), **HEADERS).status_code, 200)

    @override_settings(CONSOLE_IDLE_TIMEOUT=0)
    def test_the_bound_can_be_disabled(self):
        session = self.client.session
        session["_console_seen"] = int(time.time()) - 10_000_000
        session.save()
        self.assertEqual(self.client.get(reverse("console:root"), **HEADERS).status_code, 200)


class TestReauthentication(ConsoleAPITestCase):
    """Proof of presence, distinct from proof of identity."""

    def _request(self):
        from django.http import HttpRequest

        request = HttpRequest()
        request.session = self.client.session
        return request

    def test_absent_by_default(self):
        self.assertFalse(console_auth.reauthenticated(self._request()))

    def test_marking_makes_it_stand(self):
        request = self._request()
        console_auth.mark_reauthenticated(request)
        self.assertTrue(console_auth.reauthenticated(request))

    @override_settings(CONSOLE_REAUTH_WINDOW=1)
    def test_it_expires(self):
        request = self._request()
        console_auth.mark_reauthenticated(request)
        request.session["_console_reauth"] = int(time.time()) - 600
        self.assertFalse(console_auth.reauthenticated(request))

    def test_requiring_it_refuses_when_absent(self):
        # DRF's PermissionDenied, so the view layer renders it as a 403 with
        # its message rather than as a 500.
        from rest_framework.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            console_auth.require_reauthentication(self._request())

    def test_requiring_it_passes_when_present(self):
        request = self._request()
        console_auth.mark_reauthenticated(request)
        console_auth.require_reauthentication(request)


class TestDangerousActionsNeedPresence(ConsoleAPITestCase):
    """Proof of presence is demanded at the boundary, not inside each panel."""

    def setUp(self):
        super().setUp()
        panel_registry._reset_for_tests()
        from evennia.console.panels import register_builtin_panels

        register_builtin_panels(panel_registry)
        self.account.set_password("a-real-password-1!")
        self.account.save()
        # Changing a password rotates the session auth hash, which invalidates
        # the login done by the base setUp.
        self.client.force_login(self.account)

    def _post(self, panel, action):
        return self.client.post(reverse("console:panel-action", args=[panel, action]), **HEADERS)

    def test_the_repl_refuses_without_a_confirmation(self):
        response = self._post("repl", "execute")
        self.assertEqual(response.status_code, 403)
        self.assertIn("password", response.json()["detail"].lower())

    def test_sql_and_server_control_refuse_too(self):
        for panel, action in (("sql", "query"), ("server", "control")):
            self.assertEqual(self._post(panel, action).status_code, 403)

    def test_named_actions_elsewhere_refuse_too(self):
        # break_glass, reveal, and watch carry the same weight as a REPL even
        # though their panels do not.
        self.assertEqual(self._post("authorization", "break_glass").status_code, 403)
        self.assertEqual(self._post("moderation", "reveal").status_code, 403)
        self.assertEqual(self._post("records", "create_account").status_code, 403)

    def test_an_ordinary_action_does_not_demand_it(self):
        response = self.client.post(
            reverse("console:panel-action", args=["records", "models"]), **HEADERS
        )
        self.assertEqual(response.status_code, 200)

    def test_confirming_with_a_wrong_password_is_refused(self):
        response = self.client.post(
            reverse("console:confirm"),
            data={"password": "not it"},
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 403)

    def test_confirming_then_acting(self):
        confirmed = self.client.post(
            reverse("console:confirm"),
            data={"password": "a-real-password-1!"},
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.json()["confirmed"])
        # Now the panel's own deployment gate is what refuses, not presence.
        response = self._post("repl", "execute")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json().get("disabled"))


class TestAuthenticationIsDeclared(ConsoleAPITestCase):
    """The console names its own authentication instead of inheriting one.

    ``APIView`` reads ``DEFAULT_AUTHENTICATION_CLASSES`` once, at import, into
    a class attribute. A game whose own API is token only -- and which
    therefore drops ``SessionAuthentication`` from that setting -- left every
    console request anonymous, and DRF answered 401 with nothing in the console
    naming the cause. The page rendered, the lamps lit, and the first fetch
    failed.

    Because that read happens at import, ``override_settings`` cannot
    reproduce it: the class attribute is already bound. The test that matters
    is therefore the invariant itself -- the console declares its own -- plus
    one that patches the inherited attribute to prove the declaration is what
    is being used.
    """

    def test_the_console_declares_rather_than_inherits(self):
        # The whole fix. Inheriting means a game's API settings decide whether
        # the console's own credential is read.
        self.assertIn("authentication_classes", console_views.ConsoleView.__dict__)

    def test_session_authentication_is_declared(self):
        from rest_framework.authentication import SessionAuthentication

        self.assertIn(SessionAuthentication, console_views.ConsoleView.authentication_classes)

    def test_basic_authentication_is_not_offered(self):
        # It is in the stock defaults and has no business in front of a REPL:
        # it would put credentials on every request rather than once at sign-in.
        from rest_framework.authentication import BasicAuthentication

        self.assertNotIn(BasicAuthentication, console_views.ConsoleView.authentication_classes)

    def test_a_token_only_game_still_admits_a_signed_in_operator(self):
        """The regression, reproduced the way it actually happens."""

        from rest_framework.authentication import TokenAuthentication
        from rest_framework.views import APIView

        # Stand in for a game that narrowed the default before import time.
        with patch.object(APIView, "authentication_classes", [TokenAuthentication]):
            response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertEqual(response.status_code, 200)

    def test_a_signed_out_caller_is_still_refused(self):
        self.client.logout()
        response = self.client.get(reverse("console:root"), **HEADERS)
        self.assertIn(response.status_code, (401, 403))
