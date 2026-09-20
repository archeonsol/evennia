"""Property and fuzz coverage for policy composition, serde, and constraints."""

from django.test import SimpleTestCase, override_settings
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from evennia.authorization.engine import (
    AuthorizationContext,
    GrantScope,
    GrantSnapshot,
    ResourceSnapshot,
    evaluate,
)
from evennia.authorization.policy import (
    AllOf,
    Always,
    AnyOf,
    Never,
    Not,
    Policy,
    PredicateRequirement,
    RequiresCapability,
    policy_from_data,
    validate_grant_constraints,
)

CAPABILITIES = ["engine.object.edit", "engine.authorization.break_glass"]
PREDICATES = ["not_suspended", "setting.enabled"]
PARAM_KEYS = ["key", "flag", "mode"]

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _leaf_strategy():
    return st.one_of(
        st.just(Always()),
        st.just(Never()),
        st.builds(
            RequiresCapability,
            capability=st.sampled_from(CAPABILITIES),
            principal_scope=st.sampled_from(["effective", "account", "object", "session"]),
        ),
        st.builds(
            PredicateRequirement,
            key=st.sampled_from(PREDICATES),
            params=st.dictionaries(
                st.sampled_from(PARAM_KEYS),
                st.one_of(
                    st.booleans(), st.integers(min_value=0, max_value=9), st.text(max_size=8)
                ),
                max_size=3,
            ),
        ),
    )


def _policy_strategy():
    return st.recursive(
        _leaf_strategy(),
        lambda children: st.one_of(
            st.builds(AllOf, parts=st.lists(children, min_size=1, max_size=3).map(tuple)),
            st.builds(AnyOf, parts=st.lists(children, min_size=1, max_size=3).map(tuple)),
            st.builds(Not, inner=children),
        ),
        max_leaves=8,
    )


def _context():
    return AuthorizationContext(principal=object(), resource=object(), session=None)


def _snapshot():
    grants = {
        "engine.object.edit": frozenset({GrantScope("object:7", "world", "*")}),
        "engine.authorization.break_glass": frozenset(),
    }
    return GrantSnapshot("object:7", grants, 0)


def _resource():
    return ResourceSnapshot("object:42", "object", frozenset({"area:market"}), 0)


class PolicySerdePropertyTest(SimpleTestCase):
    @_SETTINGS
    @given(_policy_strategy())
    def test_policy_roundtrip_is_identity(self, policy: Policy):
        self.assertEqual(policy_from_data(policy.to_data()), policy)

    @_SETTINGS
    @given(
        st.dictionaries(
            st.text(max_size=6),
            st.recursive(
                st.none() | st.booleans() | st.integers() | st.text(max_size=6),
                lambda children: (
                    st.lists(children, max_size=3)
                    | st.dictionaries(st.text(max_size=4), children, max_size=3)
                ),
                max_leaves=6,
            ),
            max_size=6,
        )
    )
    def test_malformed_policy_data_fails_closed(self, payload: dict):
        data = {"schema": "auth.policy.v1", "type": "capability", "payload": payload}
        try:
            policy_from_data(data)
        except ValueError:
            return
        except Exception as err:  # noqa: BLE001 - the contract is ValueError only
            self.fail(f"policy compiler raised {type(err).__name__}: {err}")
        self.fail("malformed policy compiled instead of failing closed")


class PolicyCompositionLawTest(SimpleTestCase):
    @_SETTINGS
    @given(_policy_strategy())
    def test_always_identity_and_never_absorbing(self, policy: Policy):
        baseline = evaluate(policy, _snapshot(), _resource(), _context()).allowed
        cases = {
            "all_identity": AllOf((policy, Always())),
            "any_absorbing": AnyOf((policy, Never())),
            "double_negation": Not(Not(policy)),
        }
        for label, candidate in cases.items():
            self.assertEqual(
                evaluate(candidate, _snapshot(), _resource(), _context()).allowed,
                baseline,
                label,
            )


class ConstraintFuzzTest(SimpleTestCase):
    @override_settings(AUTHORIZATION_PREDICATE_PURITY="error")
    @_SETTINGS
    @given(
        st.dictionaries(
            st.sampled_from(["session_id", "online", "time_window", "requires_label", "unknown"]),
            st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=8)),
            max_size=4,
        )
    )
    def test_constraint_validation_is_total(self, constraints: dict):
        try:
            validated = validate_grant_constraints(constraints)
        except ValueError:
            return
        except Exception as err:  # noqa: BLE001 - the contract is ValueError only
            self.fail(f"constraint validation raised {type(err).__name__}: {err}")
        self.assertEqual(set(validated), set(constraints))
