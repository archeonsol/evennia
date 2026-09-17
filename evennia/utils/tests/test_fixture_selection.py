"""Declarative selection of the default `EvenniaTest` fixture objects."""

from django.test import SimpleTestCase

from evennia.utils.test_resources import (
    ALL_FIXTURES,
    FIXTURE_DEPENDENCIES,
    EvenniaTest,
    resolve_fixtures,
)

#: Attributes the full fixture sets, checked with `hasattr` rather than by name
#: so a new fixture cannot be added without this test noticing.
FIXTURE_ATTRIBUTES = tuple(FIXTURE_DEPENDENCIES)


class ResolveFixturesTest(SimpleTestCase):
    """Dependency expansion is what makes a narrow request safe to write."""

    def test_dependencies_are_pulled_in(self):
        self.assertEqual(resolve_fixtures({"char1"}), {"char1", "room1", "account"})
        self.assertEqual(resolve_fixtures({"exit"}), {"exit", "room1", "room2"})
        self.assertEqual(
            resolve_fixtures({"session"}), {"session", "account", "char1", "room1"}
        )

    def test_independent_fixtures_pull_in_nothing(self):
        self.assertEqual(resolve_fixtures({"script"}), {"script"})
        self.assertEqual(resolve_fixtures(set()), frozenset())

    def test_every_dependency_is_itself_a_fixture(self):
        for name, dependencies in FIXTURE_DEPENDENCIES.items():
            for dependency in dependencies:
                self.assertIn(dependency, FIXTURE_DEPENDENCIES, f"{name} -> {dependency}")

    def test_unknown_fixture_is_refused(self):
        # Silently ignoring a typo would read as "build nothing" and surface
        # much later as a missing attribute inside an unrelated assertion.
        with self.assertRaises(ValueError) as caught:
            resolve_fixtures({"chars"})
        self.assertIn("chars", str(caught.exception))
        self.assertIn("char1", str(caught.exception))


class DefaultFixtureSetTest(EvenniaTest):
    """The default is still every object, so existing tests are unaffected."""

    def test_every_fixture_is_built(self):
        self.assertEqual(self.wanted_fixtures(), ALL_FIXTURES)
        for name in FIXTURE_ATTRIBUTES:
            self.assertTrue(hasattr(self, name), f"default fixture is missing {name}")


class NarrowedFixtureSetTest(EvenniaTest):
    """A narrowed class pays for its request and its dependencies, nothing else."""

    evennia_fixtures = {"char1"}

    def test_requested_fixtures_and_dependencies_exist(self):
        self.assertEqual(self.wanted_fixtures(), {"char1", "room1", "account"})
        self.assertEqual(self.char1.location, self.room1)
        self.assertEqual(self.char1.account, self.account)

    def test_unrequested_fixtures_are_never_built(self):
        for name in ("account2", "char2", "room2", "exit", "obj1", "obj2", "script", "session"):
            self.assertFalse(hasattr(self, name), f"{name} was built but not requested")


class ScriptOnlyFixtureTest(EvenniaTest):
    """The narrowest useful case: no account, no room, no character."""

    evennia_fixtures = {"script"}

    def test_only_the_script_exists(self):
        self.assertIsNotNone(self.script)
        for name in ("account", "char1", "room1", "session"):
            self.assertFalse(hasattr(self, name), f"{name} was built but not requested")


class NoFixturesTest(EvenniaTest):
    """An empty set is legal, and tearDown must survive it.

    This is the case that catches an unguarded teardown: the old one removed
    `self.session` from the session handler unconditionally.
    """

    evennia_fixtures = set()

    def test_nothing_is_built(self):
        for name in FIXTURE_ATTRIBUTES:
            self.assertFalse(hasattr(self, name), f"{name} was built but not requested")
