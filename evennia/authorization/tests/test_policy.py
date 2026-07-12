"""Structured authorization policy tests."""

from django.test import SimpleTestCase

from evennia.authorization.policy import (
    AllOf,
    Always,
    AnyOf,
    Never,
    PolicyRegistry,
    PolicyTemplate,
    PredicateRequirement,
    RequiresCapability,
    policy_from_data,
)


class PolicyTest(SimpleTestCase):
    """Policies are immutable, serializable data rather than expressions."""

    def test_round_trip(self):
        policy = AllOf(
            (
                RequiresCapability("engine.object.edit"),
                AnyOf((Always(), PredicateRequirement("not_suspended", {"mode": "safe"}))),
            )
        )
        self.assertEqual(policy_from_data(policy.to_data()), policy)

    def test_unknown_node_is_rejected(self):
        with self.assertRaises(ValueError):
            policy_from_data({"schema": "auth.policy.v1", "type": "python_eval"})

    def test_empty_composites_are_rejected(self):
        with self.assertRaises(ValueError):
            AllOf(())
        with self.assertRaises(ValueError):
            AnyOf(())

    def test_unbounded_wire_policy_is_rejected(self):
        data = {"schema": "auth.policy.v1", "type": "always"}
        for _ in range(18):
            data = {"schema": "auth.policy.v1", "type": "not", "inner": data}
        with self.assertRaises(ValueError):
            policy_from_data(data)

    def test_constants_are_explicit(self):
        self.assertNotEqual(Always().to_data(), Never().to_data())

    def test_inheritance_prefers_explicit_resource_template(self):
        registry = PolicyRegistry()
        registry.register(PolicyTemplate("engine.closed", {"view": Never()}))
        registry.register(PolicyTemplate("engine.public", {"view": Always()}))
        registry.bind_kind("object", "engine.closed")
        resource = type("Resource", (), {"authorization_policy_template": "engine.public"})()
        self.assertIsInstance(registry.resolve(resource, "object", "view"), Always)
