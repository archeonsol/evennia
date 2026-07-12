"""Capability registry tests."""

from django.test import SimpleTestCase, override_settings

from evennia.authorization.capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    InvalidCapability,
)


class CapabilityRegistryTest(SimpleTestCase):
    """The registry is namespaced, bundle-based, and hierarchy-free."""

    def test_register_and_expand_bundle_without_rank_implication(self):
        registry = CapabilityRegistry()
        edit = registry.register(CapabilityDefinition("engine.object.edit"))
        delete = registry.register(CapabilityDefinition("engine.object.delete", sensitive=True))
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

    @override_settings(AUTHORIZATION_CAPABILITY_MODULES=("missing.module",))
    def test_invalid_registration_module_fails_boot_load(self):
        registry = CapabilityRegistry()
        with self.assertRaises(ImportError):
            registry.load_modules()
