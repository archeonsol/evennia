"""R3F capability-only runtime acceptance tests."""

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from evennia.actions import HasCapability
from evennia.authorization.policy import Always
from evennia.authorization.storage import grant_capability
from evennia.commands.command import Command
from evennia.objects.models import ObjectDB
from evennia.server.models import AuthorizationPolicyOverride


class CapabilityOnlyRuntimeTest(TestCase):
    """The retired authority surfaces cannot affect live decisions."""

    def test_action_predicate_reads_explicit_grant(self):
        principal = ObjectDB.objects.create(db_key="operator")
        grant_capability(
            f"object:{principal.pk}",
            "engine.world.build",
            scope_kind="world",
            scope_key="*",
        )

        self.assertTrue(HasCapability("engine.world.build")(None, principal))

    def test_command_metaclass_rejects_lock_authoring(self):
        with self.assertRaises(TypeError):

            class InvalidCommand(Command):
                key = "invalid"
                locks = "cmd:all()"

    def test_missing_policy_ignores_legacy_default_argument(self):
        principal = ObjectDB.objects.create(db_key="principal")
        resource = ObjectDB.objects.create(db_key="resource")

        self.assertFalse(resource.access(principal, "undefined", default=True))

    def test_typed_policy_override_round_trip(self):
        principal = ObjectDB.objects.create(db_key="principal")
        resource = ObjectDB.objects.create(db_key="resource")
        resource.policies.set("wave", Always())

        self.assertTrue(resource.access(principal, "wave"))
        self.assertIsInstance(resource.policies.get("wave"), Always)

    def test_deployment_audit_accepts_empty_legacy_storage(self):
        call_command("auth_audit_capabilities", verbosity=0)

    def test_deployment_audit_rejects_legacy_storage(self):
        ObjectDB.objects.create(db_key="legacy", db_lock_storage="view:all()")
        with self.assertRaises(CommandError):
            call_command("auth_audit_capabilities", verbosity=0)

    def test_deployment_audit_rejects_frozen_checkpoint(self):
        AuthorizationPolicyOverride.objects.create(
            resource_ref="object:999999",
            access_type="__frozen__",
            template_key="migration.empty",
            policy={},
            legacy_frozen=True,
        )
        with self.assertRaises(CommandError):
            call_command("auth_audit_capabilities", verbosity=0)

    @override_settings(
        AUTHORIZATION_PERMISSION_MIGRATION={
            "Builder": ("engine.world.build",),
        }
    )
    def test_finalizer_materializes_and_removes_legacy_authority(self):
        principal = ObjectDB.objects.create(db_key="legacy-builder")
        principal.permissions.add("Builder")
        principal.db_lock_storage = "view:all()"
        principal.save(update_fields=["db_lock_storage"])
        checkpoint = AuthorizationPolicyOverride.objects.create(
            resource_ref=f"object:{principal.pk}",
            access_type="view",
            template_key="legacy.compiled",
            policy=Always().to_data(),
            legacy_shadow="view:all()",
            legacy_frozen=True,
        )
        authored = AuthorizationPolicyOverride.objects.create(
            resource_ref=f"object:{principal.pk}",
            access_type="wave",
            template_key="authored.typed",
            policy=Always().to_data(),
        )

        call_command(
            "auth_finalize_capabilities",
            apply=True,
            remove_authority_tags=True,
            clear_lock_storage=True,
            verbosity=0,
        )

        self.assertEqual(
            ObjectDB.objects.filter(pk=principal.pk)
            .values_list("db_lock_storage", flat=True)
            .get(),
            "",
        )
        self.assertNotIn("Builder", principal.permissions.all())
        self.assertTrue(principal.has_capability("engine.world.build"))
        self.assertFalse(
            AuthorizationPolicyOverride.objects.filter(pk=checkpoint.pk).exists()
        )
        self.assertTrue(
            AuthorizationPolicyOverride.objects.filter(pk=authored.pk).exists()
        )
