"""Tests for the built-in panels.

The Records tests focus on the two rules that make it correct rather than on
its happy path: it must never partially load an idmapper model, and it must
refuse to generically write anything a domain service owns. Both fail silently
when broken -- the first in development, the second only when an invariant is
already corrupt -- so both are asserted directly.

"""

from unittest.mock import patch

from django.core.exceptions import FieldError, PermissionDenied
from django.test import TestCase, override_settings

from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels import BUILTIN_PANELS, register_builtin_panels
from evennia.console.panels.records import MAX_PAGE_SIZE, RecordsPanel
from evennia.console.panels.settings import SettingsPanel, is_secret
from evennia.console.registry import CONSOLE_ACCESS, IOContext, PanelRegistry, WorkerContext


def _ctx(**params):
    """Build a worker context carrying panel parameters."""
    return WorkerContext(
        actor_id=1,
        actor_name="tester",
        capabilities=frozenset({CONSOLE_ACCESS}),
        params=params,
    )


class TestBuiltinRegistration(TestCase):
    """The engine's own panels register cleanly."""

    def test_all_builtins_register(self):
        registry = PanelRegistry()
        register_builtin_panels(registry)
        self.assertEqual(
            [panel.key for panel in registry.panels()],
            [
                "actions",
                "attributes",
                "audit",
                "authorization",
                "errors",
                "health",
                "hooks",
                "logs",
                "migrations",
                "moderation",
                "objects",
                "prototypes",
                "records",
                "repl",
                "runtime",
                "server",
                "sessions",
                "settings",
                "sql",
            ],
        )

    #: The only panels allowed to be unusable when the game server is down.
    #: Both genuinely need live in-process state -- a Python namespace and the
    #: session handler -- and neither has a meaningful read without it. Every
    #: other panel keeps working during an outage, which is the point of
    #: degraded mode, so a new name here is a deliberate act rather than an
    #: oversight.
    MAY_NEED_IO = {"repl", "sessions"}

    def test_only_the_named_panels_need_the_io_owner(self):
        for panel in BUILTIN_PANELS:
            if panel.key in self.MAY_NEED_IO:
                continue
            self.assertFalse(panel.needs_io, f"{panel.key} would break degraded mode")

    def test_the_exceptions_are_still_the_exceptions(self):
        # Guards the list above against quietly growing.
        needing = {panel.key for panel in BUILTIN_PANELS if panel.needs_io}
        self.assertEqual(needing, self.MAY_NEED_IO)

    def test_a_game_cannot_silently_shadow_a_builtin(self):
        from evennia.console.registry import Panel, PanelError

        class Shadow(Panel):
            key = "records"
            label = "Not the real one"

        registry = PanelRegistry()
        register_builtin_panels(registry)
        with self.assertRaises(PanelError):
            registry.register(Shadow)


class TestRecordsReads(TestCase):
    """Listing, paging, ordering, and search."""

    def setUp(self):
        self.panel = RecordsPanel()

    def test_lists_a_plain_model(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent"))
        self.assertEqual(result["model"], "console.consoleauditevent")
        self.assertEqual(result["storage"], "plain")
        self.assertIn("rows", result)

    def test_lists_an_idmapper_model(self):
        result = self.panel.rows(_ctx(model="objects.objectdb"))
        self.assertEqual(result["storage"], "idmapper")

    def test_reads_never_use_a_partial_load(self):
        # only()/defer() cannot construct an uncached SharedMemoryModel, and on
        # a cached one they silently return stale fields. The lens must use
        # values() instead, on every model.
        from django.db.models.query import QuerySet

        with (
            patch.object(QuerySet, "only", side_effect=AssertionError("only() is forbidden")),
            patch.object(QuerySet, "defer", side_effect=AssertionError("defer() is forbidden")),
        ):
            for label in ("objects.objectdb", "accounts.accountdb", "server.sanction"):
                self.panel.rows(_ctx(model=label))
                # detail() reads every field, which is where a partial load
                # would be most tempting.
                with self.assertRaises(LookupError):
                    self.panel.detail(_ctx(model=label), 999999)

    def test_page_size_is_capped(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", page_size=100000))
        self.assertEqual(result["page_size"], MAX_PAGE_SIZE)

    def test_a_page_carries_no_total(self):
        # Paging is by cursor and never counts; a total is asked for
        # separately. See test_records_scale for the full contract.
        result = self.panel.rows(_ctx(model="console.consoleauditevent"))
        self.assertNotIn("total", result)
        self.assertIn("has_more", result)

    def test_page_size_survives_nonsense(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", page_size="banana"))
        self.assertTrue(1 <= result["page_size"] <= MAX_PAGE_SIZE)

    def test_unknown_ordering_field_is_rejected(self):
        with self.assertRaises(FieldError):
            self.panel.rows(_ctx(model="console.consoleauditevent", order="not_a_field"))

    def test_ordering_accepts_descending(self):
        result = self.panel.rows(_ctx(model="console.consoleauditevent", order="-panel"))
        self.assertEqual(result["order"], "-panel")

    def test_missing_model_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.rows(_ctx())
        with self.assertRaises(LookupError):
            self.panel.rows(_ctx(model="nope.nothing"))

    def test_search_matches_by_prefix(self):
        ConsoleAuditEvent.objects.create(event_id="a" * 32, panel="findme", operation="x")
        ConsoleAuditEvent.objects.create(event_id="b" * 32, panel="other", operation="x")
        result = self.panel.rows(_ctx(model="console.consoleauditevent", search="findme"))
        self.assertEqual(len(result["rows"]), 1)

    def test_detail_returns_one_record(self):
        row = ConsoleAuditEvent.objects.create(event_id="c" * 32, panel="p", operation="o")
        result = self.panel.detail(_ctx(model="console.consoleauditevent"), row.pk)
        self.assertEqual(result["record"]["event_id"], "c" * 32)
        self.assertTrue(result["fields"])

    def test_detail_missing_row_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(model="console.consoleauditevent"), 999999)

    def test_detail_bad_identifier_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(model="console.consoleauditevent"), "not-a-pk")

    def test_values_are_json_safe(self):
        ConsoleAuditEvent.objects.create(event_id="d" * 32, panel="p", operation="o")
        result = self.panel.rows(_ctx(model="console.consoleauditevent"))
        for row in result["rows"]:
            for value in row.values():
                self.assertIsInstance(value, (str, int, float, bool, type(None)))

    def test_models_listing_carries_the_write_policy(self):
        listing = {item["label"]: item for item in self.panel.models(_ctx())}
        self.assertFalse(listing["server.sanction"]["writable"])
        self.assertIn("hash chain", listing["server.sanction"]["write_via"])
        self.assertTrue(listing["objects.objectdb"]["writable"])


class TestRecordsWrites(TestCase):
    """Domain-owned models refuse the generic writer."""

    def setUp(self):
        self.panel = RecordsPanel()
        self.ctx = IOContext(actor_id=1, actor_name="tester", capabilities=frozenset())

    def test_domain_owned_model_refuses_deletion(self):
        with self.assertRaises(PermissionDenied) as caught:
            self.panel.delete(self.ctx, model="server.sanction", ids=[1])
        # The refusal names where the mutation does belong.
        self.assertIn("issue_sanction", str(caught.exception))

    def test_audit_trail_refuses_its_own_deletion(self):
        with self.assertRaises(PermissionDenied):
            self.panel.delete(self.ctx, model="console.consoleauditevent", ids=[1])

    def test_tag_refuses_deletion(self):
        with self.assertRaises(PermissionDenied):
            self.panel.delete(self.ctx, model="typeclasses.tag", ids=[1])

    def test_empty_id_list_is_a_no_op(self):
        result = self.panel.delete(self.ctx, model="objects.objectdb", ids=[])
        self.assertEqual(result["deleted"], [])

    def test_delete_writes_an_audit_row(self):
        from evennia.console.services import AdminDeleteResult

        with patch(
            "evennia.console.panels.records.delete_admin",
            return_value=AdminDeleteResult(status="ok", deleted_ids=(4,)),
        ):
            self.panel.delete(self.ctx, model="objects.objectdb", ids=[4])
        row = ConsoleAuditEvent.objects.get(panel="records", operation="delete")
        self.assertEqual(row.actor_id, 1)
        self.assertEqual(row.after["deleted"], [4])
        self.assertIn("objects.objectdb#4", row.target_ref)


class TestSettingsPanel(TestCase):
    """Masking happens in the service, and masked rows stay visible."""

    def setUp(self):
        self.panel = SettingsPanel()

    def test_secret_detection(self):
        for name in ("SECRET_KEY", "EMAIL_HOST_PASSWORD", "DATABASES", "MODERATION_HASH_SALT"):
            self.assertTrue(is_secret(name), f"{name} should be masked")
        for name in ("TIME_ZONE", "CONSOLE_ENABLED", "SERVERNAME"):
            self.assertFalse(is_secret(name), f"{name} should not be masked")

    def test_secret_values_never_appear(self):
        result = self.panel.rows(_ctx())
        rows = {row["name"]: row for row in result["rows"]}
        self.assertEqual(rows["SECRET_KEY"]["value"], "<masked>")
        self.assertTrue(rows["SECRET_KEY"]["masked"])

    def test_masked_settings_are_still_listed(self):
        # "not set" and "not shown" must stay distinguishable.
        rows = {row["name"] for row in self.panel.rows(_ctx())["rows"]}
        self.assertIn("SECRET_KEY", rows)

    def test_no_secret_value_leaks_anywhere_in_the_payload(self):
        from django.conf import settings as django_settings

        needle = str(django_settings.SECRET_KEY)
        if len(needle) < 8:
            self.skipTest("test settings use a trivial secret key")
        self.assertNotIn(needle, str(self.panel.rows(_ctx())))

    @override_settings(CONSOLE_ENABLED=False)
    def test_overrides_are_marked(self):
        rows = {row["name"]: row for row in self.panel.rows(_ctx())["rows"]}
        self.assertTrue(rows["CONSOLE_ENABLED"]["overridden"])
        self.assertEqual(rows["CONSOLE_ENABLED"]["source"], "game")

    def test_engine_defaults_are_marked(self):
        rows = {row["name"]: row for row in self.panel.rows(_ctx())["rows"]}
        self.assertTrue(rows["CONSOLE_AUDIT_RETENTION_DAYS"]["has_default"])


class TestMigrationsPanel(TestCase):
    """Pending migrations and the missing-migration check."""

    def setUp(self):
        from evennia.console.panels.migrations import MigrationsPanel

        self.panel = MigrationsPanel()

    def test_lists_migrations_with_applied_state(self):
        result = self.panel.rows(_ctx())
        self.assertTrue(result["rows"])
        row = result["rows"][0]
        self.assertIn("app", row)
        self.assertIn("applied", row)

    def test_console_migration_is_present(self):
        apps_seen = {row["app"] for row in self.panel.rows(_ctx())["rows"]}
        self.assertIn("console", apps_seen)

    def test_reports_a_pending_count(self):
        result = self.panel.rows(_ctx())
        self.assertEqual(result["pending_count"], len(result["pending"]))

    def test_missing_migration_check_reports_availability(self):
        result = self.panel.rows(_ctx())
        self.assertIn("available", result["missing"])


class TestHealthPanel(TestCase):
    """Health renders per-check rows."""

    def setUp(self):
        from evennia.console.panels.health import HealthPanel

        self.panel = HealthPanel()

    def test_reports_each_check(self):
        result = self.panel.rows(_ctx())
        checks = {row["check"] for row in result["rows"]}
        self.assertEqual(checks, {"database", "io_owner"})

    def test_degraded_when_the_io_owner_is_gone(self):
        with patch("evennia.console.health.io_available", return_value=False):
            result = self.panel.rows(_ctx())
        self.assertTrue(result["degraded"])
        self.assertFalse(result["healthy"])
