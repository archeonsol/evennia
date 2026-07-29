"""Authorization evaluator and cache tests."""

from dataclasses import dataclass
from unittest.mock import patch

from django.test import SimpleTestCase

from evennia.authorization.engine import (
    AuthorizationContext,
    GrantSnapshot,
    ResourceSnapshot,
    evaluate,
)
from evennia.authorization.policy import AllOf, PredicateRequirement, RequiresCapability


@dataclass
class FakePrincipal:
    """Minimal principal for pure evaluator tests."""

    id: int = 7
    account: object = None


@dataclass
class FakeAccount:
    """Account wrapper whose durable identity is its database primary key."""

    pk: int


@dataclass
class FakeControlledResource:
    """Minimal resource with one durable owning account."""

    account: FakeAccount


class EvaluatorTest(SimpleTestCase):
    """Grant and resource state join without hierarchy or ownership."""

    def test_scoped_grant_satisfies_requirement(self):
        grants = GrantSnapshot(
            principal_ref="object:7",
            by_capability={"engine.object.edit": frozenset({("label", "project:market")})},
            generation=1,
        )
        resource = ResourceSnapshot(
            resource_ref="object:42",
            resource_kind="object",
            labels=frozenset({"project:market", "object:42"}),
            generation=2,
        )
        decision = evaluate(
            AllOf(
                (
                    RequiresCapability("engine.object.edit"),
                    PredicateRequirement("not_suspended"),
                )
            ),
            grants,
            resource,
            AuthorizationContext(principal=FakePrincipal(), resource=object()),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason_code, "requirements_satisfied")

    def test_no_creator_or_owner_shortcut_exists(self):
        grants = GrantSnapshot("object:7", {}, 1)
        resource = ResourceSnapshot("object:42", "object", frozenset({"object:42"}), 1)
        context = AuthorizationContext(
            principal=FakePrincipal(),
            resource=type("Resource", (), {"created_by_id": 7})(),
        )
        decision = evaluate(RequiresCapability("engine.object.edit"), grants, resource, context)
        self.assertFalse(decision.allowed)

    def test_dynamic_predicate_is_action_memoized(self):
        calls = []

        def provider(context, params):
            calls.append(context)
            return True

        context = AuthorizationContext(principal=FakePrincipal(), resource=object())
        policy = AllOf(
            (
                PredicateRequirement("test.once"),
                PredicateRequirement("test.once"),
            )
        )
        with patch(
            "evennia.authorization.engine.get_predicate_provider",
            return_value=provider,
        ):
            decision = evaluate(
                policy,
                GrantSnapshot("object:7", {}, 1),
                ResourceSnapshot("object:42", "object", frozenset(), 1),
                context,
            )
        self.assertTrue(decision.allowed)
        self.assertEqual(len(calls), 1)

    def test_controls_resource_compares_durable_account_identity(self):
        principal_account = FakeAccount(pk=7)
        resource_account = FakeAccount(pk=7)
        self.assertIsNot(principal_account, resource_account)
        decision = evaluate(
            PredicateRequirement("principal.controls_resource"),
            GrantSnapshot("account:7", {}, 1),
            ResourceSnapshot("object:42", "object", frozenset(), 1),
            AuthorizationContext(
                principal=FakePrincipal(account=principal_account),
                resource=FakeControlledResource(account=resource_account),
            ),
        )
        self.assertTrue(decision.allowed)

    def test_controls_resource_denies_foreign_account(self):
        decision = evaluate(
            PredicateRequirement("principal.controls_resource"),
            GrantSnapshot("account:8", {}, 1),
            ResourceSnapshot("object:42", "object", frozenset(), 1),
            AuthorizationContext(
                principal=FakePrincipal(account=FakeAccount(pk=8)),
                resource=FakeControlledResource(account=FakeAccount(pk=7)),
            ),
        )
        self.assertFalse(decision.allowed)

    def test_controls_resource_does_not_bypass_additional_capability(self):
        account = FakeAccount(pk=7)
        decision = evaluate(
            AllOf(
                (
                    PredicateRequirement("principal.controls_resource"),
                    RequiresCapability("game.staff.puppet"),
                )
            ),
            GrantSnapshot("account:7", {}, 1),
            ResourceSnapshot("object:42", "object", frozenset(), 1),
            AuthorizationContext(
                principal=FakePrincipal(account=account),
                resource=FakeControlledResource(account=FakeAccount(pk=7)),
            ),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failed_requirements, ("missing:game.staff.puppet",))
