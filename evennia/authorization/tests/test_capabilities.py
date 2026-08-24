"""Capability registry tests."""

from django.test import SimpleTestCase, override_settings

from evennia.authorization.capabilities import (
    BundleDefinition,
    CapabilityDefinition,
    CapabilityRegistry,
    InvalidCapability,
)


class CapabilityRegistryTest(SimpleTestCase):
    """The registry is namespaced, bundle-based, and hierarchy-free."""

    def test_register_and_expand_bundle_without_rank_implication(self):
        registry = CapabilityRegistry()
        edit = registry.register(CapabilityDefinition("engine.object.edit"))
        delete = registry.register(
            CapabilityDefinition("engine.object.delete", sensitive=True)
        )
        registry.register_bundle("world_builder", (edit.key, delete.key))

        self.assertEqual(
            registry.expand_bundle("world_builder"),
            frozenset({edit.key, delete.key}),
        )
        self.assertNotIn("engine.object.view", registry.expand_bundle("world_builder"))

    def test_unknown_capability_fails_validation(self):
        registry = CapabilityRegistry()
        with self.assertRaises(InvalidCapability):
            registry.require("engine.object.typo")

    def test_capability_metadata_is_preserved(self):
        registry = CapabilityRegistry()
        definition = registry.register(
            CapabilityDefinition(
                "game.ticket.assist",
                description="Handle ordinary support tickets.",
                category="Support",
                status="active",
            )
        )

        self.assertEqual(definition.category, "Support")
        self.assertEqual(definition.status, "active")
        self.assertTrue(definition.description)

    def test_bundle_metadata_is_preserved_without_changing_expansion(self):
        registry = CapabilityRegistry()
        capability = registry.register(CapabilityDefinition("game.ticket.assist"))
        registered = registry.register_bundle(
            "support",
            (capability.key,),
            description="Front-line support authority.",
            category="Staff ranks",
        )

        self.assertEqual(registered, frozenset({capability.key}))
        definition = registry.bundle_definitions()[0]
        self.assertIsInstance(definition, BundleDefinition)
        self.assertEqual(definition.key, "support")
        self.assertEqual(definition.description, "Front-line support authority.")
        self.assertEqual(definition.category, "Staff ranks")

    def test_unknown_lifecycle_status_is_rejected(self):
        with self.assertRaises(InvalidCapability):
            CapabilityDefinition("game.ticket.assist", status="forgotten")

    @override_settings(AUTHORIZATION_CAPABILITY_MODULES=("missing.module",))
    def test_invalid_registration_module_fails_boot_load(self):
        registry = CapabilityRegistry()
        with self.assertRaises(ImportError):
            registry.load_modules()
