"""Tests for the hook registry lint surface."""

from evennia.hooks import hook
from evennia.hooks.lint import _check_class, lint
from evennia.hooks.registry import _REGISTRY, _reset_registry_for_tests
from evennia.utils.test_resources import EvenniaTestCase


class CheckClassTest(EvenniaTestCase):
    """``_check_class`` finds undecorated hook-name methods on a class."""

    def setUp(self):
        super().setUp()
        self._saved_registry = dict(_REGISTRY)
        _reset_registry_for_tests()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved_registry)
        super().tearDown()

    def test_missing_decorator_flagged(self):
        class _Bare:
            def at_pre_move(self, *a, **k):
                pass

        findings = _check_class(_Bare)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "MISSING_DECORATOR")
        self.assertIn("at_pre_move", findings[0].message)

    def test_decorated_method_not_flagged(self):
        class _Good:
            @hook(
                event="move",
                phase="pre",
                actor="mover",
                returns="veto",
                discipline="public",
                fires_from=(),
            )
            def at_pre_move(self, *a, **k):
                pass

        self.assertEqual(_check_class(_Good), [])

    def test_inherited_spec_not_flagged(self):
        class _Parent:
            @hook(
                event="move",
                phase="pre",
                actor="mover",
                returns="veto",
                discipline="public",
                fires_from=(),
            )
            def at_pre_move(self, *a, **k):
                pass

        class _Child(_Parent):
            def at_pre_move(self, *a, **k):
                # Override without @hook; parent's spec inherits silently.
                pass

        self.assertEqual(_check_class(_Child), [])

    def test_non_hook_names_ignored(self):
        class _NotHooks:
            def helper(self):
                pass

            def _private(self):
                pass

            def __dunder__(self):
                pass

        self.assertEqual(_check_class(_NotHooks), [])


class SpecChecksTest(EvenniaTestCase):
    """``lint()`` validates registered specs' fires_from and phase-name match."""

    def setUp(self):
        super().setUp()
        self._saved_registry = dict(_REGISTRY)
        _reset_registry_for_tests()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved_registry)
        super().tearDown()

    def test_unresolved_fires_from(self):
        class _BadFires:
            @hook(
                event="move",
                phase="pre",
                actor="mover",
                returns="veto",
                discipline="public",
                fires_from=("NoSuchClass.no_such_method",),
            )
            def at_pre_move(self, *a, **k):
                pass

        findings = lint(classes=[])
        codes = [f.code for f in findings]
        self.assertIn("UNRESOLVED_FIRES_FROM", codes)

    def test_phase_mismatch(self):
        class _Mismatch:
            @hook(
                event="move",
                phase="post",  # name says pre, declared post
                actor="mover",
                returns="veto",
                discipline="public",
                fires_from=(),
            )
            def at_pre_move(self, *a, **k):
                pass

        findings = lint(classes=[])
        codes = [f.code for f in findings]
        self.assertIn("PHASE_MISMATCH", codes)

    def test_extends_skips_fires_from(self):
        class _Override:
            @hook(extends="LifecycleMixin.at_pre_puppet", notes="suppressed")
            def at_pre_puppet(self, *a, **k):
                pass

        findings = lint(classes=[])
        self.assertEqual(findings, [])


class PilotIntegrationTest(EvenniaTestCase):
    """End-to-end: the real LifecycleMixin.at_pre_puppet survives lint."""

    def test_pilot_hook_not_flagged(self):
        from evennia.objects.mixins.lifecycle import LifecycleMixin

        findings = lint(classes=[LifecycleMixin])
        flagged = [f for f in findings if "at_pre_puppet" in f.message]
        self.assertEqual(flagged, [])

    def test_engine_lint_is_clean(self):
        # H1c has completed; every engine hook is registered or
        # inherits silently. Lint should return zero findings.
        findings = lint()
        self.assertEqual(
            findings,
            [],
            "expected clean lint; got:\n"
            + "\n".join(f"  [{f.code}] {f.message}" for f in findings),
        )
