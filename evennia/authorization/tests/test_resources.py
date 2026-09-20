"""Resource adapter tests."""

from django.test import SimpleTestCase

from evennia.authorization.resources import ResourceAdapter, ResourceAdapterRegistry


class ResourceAdapterTest(SimpleTestCase):
    """Games can add resource families without modifying engine models."""

    def test_priority_adapter_supplies_reference_and_labels(self):
        registry = ResourceAdapterRegistry()
        registry.register(
            ResourceAdapter(
                "vehicle",
                lambda resource: hasattr(resource, "vin"),
                lambda resource: resource.vin,
                lambda resource: ("fleet:civic",),
                priority=10,
            )
        )
        resource = type("Vehicle", (), {"vin": "AV-42"})()
        adapter = registry.for_resource(resource)
        self.assertEqual(adapter.reference(resource), "AV-42")
        self.assertEqual(tuple(adapter.labels(resource)), ("fleet:civic",))

    def test_location_sensitive_labels_are_explicit(self):
        stable = ResourceAdapter("stable", lambda resource: True, lambda resource: "stable")
        moving = ResourceAdapter(
            "moving",
            lambda resource: True,
            lambda resource: "moving",
            location_sensitive=True,
        )

        self.assertFalse(stable.location_sensitive)
        self.assertTrue(moving.location_sensitive)
