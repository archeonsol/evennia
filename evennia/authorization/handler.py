"""Typed, capability-native policy authoring for generic resources."""

from __future__ import annotations

from django.db import transaction

from evennia.server.models import AuthorizationPolicyOverride

from .policy import Policy, policy_from_data, validate_policy
from .service import authorize
from .storage import bump_resource_generation, resource_ref


class PolicyHandler:
    """Read and mutate sparse typed policies for one resource."""

    def __init__(self, resource):
        """Bind the handler to a generic authorization resource."""

        self.resource = resource

    def set(self, operation: str, policy: Policy) -> Policy:
        """Author one validated operation policy without a string DSL."""

        operation = str(operation or "").strip().lower()
        if not operation or len(operation) > 64 or not isinstance(policy, Policy):
            raise ValueError("policy set requires an operation and typed Policy node")
        data = policy.to_data()
        policy_from_data(data)
        validate_policy(policy)
        with transaction.atomic():
            AuthorizationPolicyOverride.objects.update_or_create(
                resource_ref=resource_ref(self.resource),
                access_type=operation,
                defaults={
                    "template_key": "authored.typed",
                    "policy": data,
                    "legacy_shadow": "",
                    "legacy_frozen": False,
                },
            )
            # Keep same-process reads strongly consistent even inside an outer
            # transaction (notably atomic command flows and Django TestCase).
            # The commit callback republishes after durability for peers.
            bump_resource_generation(self.resource)
            transaction.on_commit(lambda: bump_resource_generation(self.resource))
        return policy

    def remove(self, operation: str) -> bool:
        """Remove one instance override, exposing any authored class default."""

        with transaction.atomic():
            deleted, _ = AuthorizationPolicyOverride.objects.filter(
                resource_ref=resource_ref(self.resource),
                access_type=str(operation).lower(),
            ).delete()
            if deleted:
                bump_resource_generation(self.resource)
                transaction.on_commit(lambda: bump_resource_generation(self.resource))
        return bool(deleted)

    def clear(self) -> int:
        """Remove every instance override for this resource."""

        with transaction.atomic():
            deleted, _ = AuthorizationPolicyOverride.objects.filter(
                resource_ref=resource_ref(self.resource)
            ).delete()
            if deleted:
                bump_resource_generation(self.resource)
                transaction.on_commit(lambda: bump_resource_generation(self.resource))
        return int(deleted)

    def get(self, operation: str) -> Policy | None:
        """Return one instance override without evaluating it."""

        data = (
            AuthorizationPolicyOverride.objects.filter(
                resource_ref=resource_ref(self.resource), access_type=str(operation).lower()
            )
            .values_list("policy", flat=True)
            .first()
        )
        return policy_from_data(data) if data else None

    def all(self) -> dict[str, Policy]:
        """Return every instance override in deterministic operation order."""

        rows = AuthorizationPolicyOverride.objects.filter(
            resource_ref=resource_ref(self.resource)
        ).values_list("access_type", "policy")
        return {
            operation: policy_from_data(data)
            for operation, data in sorted(rows)
            if data and operation != "__frozen__"
        }

    def check(self, principal, operation: str, *, default: bool = False, context=None) -> bool:
        """Evaluate through the sole capability authorization authority."""

        return authorize(
            principal, self.resource, operation, default=default, context=context
        ).allowed
