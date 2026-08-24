"""Tests for the authorization panel.

The prober is what matters. An explanation that contradicts the verdict is
worse than no explanation, because an operator will act on it: reading "no
grant carries this" while a grant plainly exists sends somebody off to issue a
duplicate, or to hunt a bug that is not there.

That failure is exactly what a silent shape mismatch produces, and this file
exists because the first version had one -- it read ``GrantScope`` records as
tuples, the exception was swallowed, and every probe reported zero grants while
correctly reporting the verdict.

"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from evennia.authorization.storage import grant_capability, principal_refs
from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.authorization import AuthorizationPanel
from evennia.console.registry import CONSOLE_ACCESS, IOContext, WorkerContext

CAPABILITY = "engine.console.access"


def _ctx(**params):
    """Build a worker context."""
    return WorkerContext(
        actor_id=1,
        actor_name="t",
        capabilities=frozenset({CONSOLE_ACCESS}),
        params=params,
    )


class AuthorizationTestCase(TestCase):
    """One account holding one capability."""

    def setUp(self):
        self.panel = AuthorizationPanel()
        self.account = get_user_model().objects.create(
            username="grantee", is_active=True
        )
        grant_capability(
            principal_refs(self.account)[0],
            CAPABILITY,
            scope_kind="world",
            scope_key="*",
            reason="test",
        )

    def io(self):
        """Return an IO context."""
        return IOContext(
            actor_id=self.account.pk, actor_name="grantee", capabilities=frozenset()
        )


class TestViews(AuthorizationTestCase):
    """Every R3 table the admin never registered."""

    def test_grants_view(self):
        result = self.panel.rows(_ctx(view="grants"))
        self.assertEqual(result["view"], "grants")
        self.assertEqual(result["model"], "server.authorizationgrant")
        self.assertTrue(result["rows"])

    def test_every_view_is_reachable(self):
        for view in ("grants", "scopes", "policies", "principals", "audit"):
            self.assertEqual(self.panel.rows(_ctx(view=view))["view"], view)

    def test_an_unknown_view_fails_closed(self):
        with self.assertRaises(LookupError):
            self.panel.rows(_ctx(view="nonsense"))

    def test_filtering_by_principal(self):
        ref = principal_refs(self.account)[0]
        self.assertTrue(self.panel.rows(_ctx(view="grants", principal=ref))["rows"])
        self.assertFalse(
            self.panel.rows(_ctx(view="grants", principal="account:99999"))["rows"]
        )

    def test_filtering_by_capability(self):
        self.assertTrue(
            self.panel.rows(_ctx(view="grants", capability=CAPABILITY))["rows"]
        )
        self.assertFalse(
            self.panel.rows(_ctx(view="grants", capability="engine.object.view"))[
                "rows"
            ]
        )

    def test_the_capability_vocabulary_is_listed(self):
        result = self.panel.rows(_ctx())
        keys = {item["key"] for item in result["capabilities"]}
        self.assertIn(CAPABILITY, keys)
        self.assertIn("engine.authorization.break_glass", keys)

    def test_console_access_is_marked_sensitive(self):
        # It carries a REPL. Anything that presents it as routine is lying.
        found = {item["key"]: item for item in self.panel.rows(_ctx())["capabilities"]}
        self.assertTrue(found[CAPABILITY]["sensitive"])
        self.assertFalse(found[CAPABILITY]["delegable"])

    def test_narrow_moderation_controls_are_registered_and_non_delegable(self):
        found = {item["key"]: item for item in self.panel.rows(_ctx())["capabilities"]}

        for capability in (
            "engine.console.moderation.address",
            "engine.console.moderation.permanent",
        ):
            self.assertTrue(found[capability]["sensitive"])
            self.assertFalse(found[capability]["delegable"])
            self.assertTrue(found[capability]["description"])

    def test_engine_vocabulary_is_hidden_by_default(self):
        found = {item["key"]: item for item in self.panel.rows(_ctx())["capabilities"]}

        self.assertFalse(found[CAPABILITY]["default_visible"])
        self.assertEqual(found[CAPABILITY]["category"], "Console")
        self.assertTrue(found[CAPABILITY]["description"])

    def test_bundles_are_expanded(self):
        names = {item["key"] for item in self.panel.rows(_ctx())["bundles"]}
        self.assertTrue(names)

    def test_bundle_vocabulary_has_descriptions_and_visibility(self):
        rows = self.panel.rows(_ctx())["bundles"]

        self.assertTrue(all(item["description"] for item in rows))
        self.assertTrue(all("default_visible" in item for item in rows))

    def test_it_survives_degraded_mode(self):
        self.assertFalse(AuthorizationPanel.needs_io)


class TestProbe(AuthorizationTestCase):
    """The verdict, and an explanation that agrees with it."""

    def test_a_held_capability_is_allowed(self):
        result = self.panel.probe(
            self.io(), principal_id=self.account.pk, capability=CAPABILITY
        )
        self.assertTrue(result["allowed"])

    def test_the_explanation_names_the_grants(self):
        # The regression this file exists for: grants found, and said so.
        result = self.panel.probe(
            self.io(), principal_id=self.account.pk, capability=CAPABILITY
        )
        self.assertTrue(result["matching_grants"])
        self.assertIn("grant", result["explanation"])
        self.assertNotIn("no direct grant", result["explanation"])

    def test_the_grant_scope_is_reported(self):
        result = self.panel.probe(
            self.io(), principal_id=self.account.pk, capability=CAPABILITY
        )
        scopes = {
            (row["scope_kind"], row["scope_key"]) for row in result["matching_grants"]
        }
        self.assertIn(("world", "*"), scopes)

    def test_an_unheld_capability_is_denied_and_explained(self):
        result = self.panel.probe(
            self.io(), principal_id=self.account.pk, capability="engine.runtime.manage"
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["matching_grants"], [])
        self.assertIn("no grant", result["explanation"])

    def test_the_explanation_never_contradicts_the_verdict(self):
        # Allowed with grants must not read as "no grant found", and denied
        # must not read as allowed. An operator acts on this sentence.
        for capability in (CAPABILITY, "engine.runtime.manage", "engine.object.view"):
            result = self.panel.probe(
                self.io(), principal_id=self.account.pk, capability=capability
            )
            explanation = result["explanation"].lower()
            if result["allowed"]:
                self.assertTrue(explanation.startswith("allowed"), explanation)
            else:
                self.assertTrue(explanation.startswith("denied"), explanation)
            if result["allowed"] and result["matching_grants"]:
                self.assertNotIn("no direct grant", explanation)

    def test_the_principal_refs_are_shown(self):
        result = self.panel.probe(
            self.io(), principal_id=self.account.pk, capability=CAPABILITY
        )
        self.assertIn(principal_refs(self.account)[0], result["refs"])

    def test_an_unknown_principal_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.probe(self.io(), principal_id=999999, capability=CAPABILITY)

    def test_an_unknown_capability_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.probe(
                self.io(), principal_id=self.account.pk, capability="not.a.capability"
            )

    def test_probing_is_audited(self):
        self.panel.probe(self.io(), principal_id=self.account.pk, capability=CAPABILITY)
        row = ConsoleAuditEvent.objects.get(panel="authorization", operation="probe")
        self.assertEqual(row.after["capability"], CAPABILITY)


class TestBreakGlass(AuthorizationTestCase):
    """Temporary bypass: reason required, lifetime bounded, recorded forever."""

    def test_issues_a_bounded_grant(self):
        result = self.panel.break_glass(
            self.io(),
            principal_id=self.account.pk,
            reason="locked out mid-incident",
            ttl_seconds=300,
        )
        self.assertEqual(result["ttl_seconds"], 300)
        self.assertTrue(result["expires_at"])

    def test_a_reason_is_required(self):
        with self.assertRaises(ValueError):
            self.panel.break_glass(self.io(), principal_id=self.account.pk, reason="  ")

    def test_the_lifetime_is_bounded(self):
        for ttl in (10, 100000):
            with self.assertRaises(ValueError):
                self.panel.break_glass(
                    self.io(), principal_id=self.account.pk, reason="r", ttl_seconds=ttl
                )

    def test_an_unknown_principal_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.break_glass(self.io(), principal_id=999999, reason="r")

    def test_it_is_audited_permanently(self):
        # The row an investigation wants, and the one an attacker would most
        # want gone.
        self.panel.break_glass(
            self.io(), principal_id=self.account.pk, reason="incident 4"
        )
        row = ConsoleAuditEvent.objects.get(
            panel="authorization", operation="break_glass_issue"
        )
        self.assertEqual(row.retention, ConsoleAuditEvent.RETENTION_PERMANENT)
        self.assertEqual(row.message, "incident 4")

    def test_it_says_what_it_does_not_bypass(self):
        result = self.panel.break_glass(
            self.io(), principal_id=self.account.pk, reason="r"
        )
        self.assertIn("Domain invariants still hold", result["note"])
