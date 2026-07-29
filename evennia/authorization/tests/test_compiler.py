"""Legacy lock compiler tests."""

from django.test import SimpleTestCase

from evennia.authorization.legacy_import.compiler import CompilationError, compile_lockstring
from evennia.authorization.policy import AllOf, AnyOf, PredicateRequirement, RequiresCapability


class CompilerTest(SimpleTestCase):
    """The one-way compiler produces policies and explicit synthesized grants."""

    def test_perm_compiles_to_namespaced_capability(self):
        result = compile_lockstring("edit:perm(Builder)")
        self.assertIsInstance(result.policies["edit"], RequiresCapability)
        self.assertEqual(result.policies["edit"].capability, "legacy.permission.builder")

    def test_id_compiles_to_resource_grant_not_ownership(self):
        result = compile_lockstring("control:id(123)", resource_ref="object:44")
        self.assertEqual(result.policies["control"].capability, "engine.object.control")
        self.assertEqual(result.grants[0].principal_ref, "entity:123")
        self.assertEqual(result.grants[0].scope_key, "object:44")
        self.assertEqual(result.grants[0].provenance, "legacy_id_lock")

    def test_registered_contextual_lock_becomes_provider_requirement(self):
        result = compile_lockstring("traverse:tag(vip,access)")
        self.assertIsInstance(result.policies["traverse"], PredicateRequirement)
        self.assertEqual(result.policies["traverse"].key, "legacy.core_lockfunc")

    def test_unknown_lockfunc_is_reported_not_silently_kept(self):
        with self.assertRaises(CompilationError):
            compile_lockstring("edit:arbitrary_python(foo)")

    def test_malformed_identity_preserves_false_with_warning(self):
        result = compile_lockstring("puppet:id(Watcher)", resource_ref="object:44")
        self.assertTrue(result.warnings)
        self.assertEqual(result.policies["puppet"].to_data()["type"], "never")

    def test_boolean_grouping_is_preserved(self):
        result = compile_lockstring(
            "edit:(perm(Builder) or id(123)) and tag(project)",
            resource_ref="object:44",
        )
        policy = result.policies["edit"]
        self.assertIsInstance(policy, AllOf)
        self.assertIsInstance(policy.parts[0], AnyOf)
