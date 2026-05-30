"""Tests for the @hook decorator and registry lookup surface."""

from evennia.hooks import HookSpec, describe, for_event, hook, list_all
from evennia.hooks.registry import _REGISTRY, _reset_registry_for_tests
from evennia.utils.test_resources import EvenniaTestCase


class HookSpecTest(EvenniaTestCase):
    """HookSpec dataclass field validation."""

    def test_minimal_spec_fields(self):
        spec = HookSpec(
            event="puppet",
            phase="pre",
            actor="target",
            returns="veto",
            discipline="public",
            fires_from=("DefaultAccount.puppet_object",),
        )
        self.assertEqual(spec.event, "puppet")
        self.assertEqual(spec.phase, "pre")
        self.assertEqual(spec.fires_from, ("DefaultAccount.puppet_object",))
        self.assertIsNone(spec.notes)
        self.assertIsNone(spec.extends)

    def test_rejects_unknown_phase(self):
        with self.assertRaises(ValueError):
            HookSpec(
                event="puppet",
                phase="bogus",
                actor="target",
                returns="veto",
                discipline="public",
                fires_from=(),
            )

    def test_rejects_unknown_returns(self):
        with self.assertRaises(ValueError):
            HookSpec(
                event="puppet",
                phase="pre",
                actor="target",
                returns="bogus",
                discipline="public",
                fires_from=(),
            )

    def test_rejects_unknown_discipline(self):
        with self.assertRaises(ValueError):
            HookSpec(
                event="puppet",
                phase="pre",
                actor="target",
                returns="veto",
                discipline="bogus",
                fires_from=(),
            )

    def test_rejects_unknown_cache_state(self):
        with self.assertRaises(ValueError):
            HookSpec(
                event="puppet",
                phase="pre",
                actor="target",
                returns="veto",
                discipline="public",
                fires_from=(),
                state_cache_state="bogus",
            )


class HookDecoratorTest(EvenniaTestCase):
    """@hook decorator attaches metadata and registers."""

    def setUp(self):
        super().setUp()
        self._saved_registry = dict(_REGISTRY)
        _reset_registry_for_tests()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved_registry)
        super().tearDown()

    def test_decorator_attaches_spec(self):
        class _Demo:
            @hook(
                event="puppet",
                phase="pre",
                actor="target",
                returns="veto",
                discipline="public",
                fires_from=("DefaultAccount.puppet_object",),
            )
            def at_pre_puppet(self, account, session=None, **kwargs):
                pass

        self.assertIsInstance(_Demo.at_pre_puppet.__evennia_hook__, HookSpec)
        self.assertEqual(_Demo.at_pre_puppet.__evennia_hook__.event, "puppet")

    def test_decorator_registers_in_module_dict(self):
        class _Demo2:
            @hook(
                event="move",
                phase="pre",
                actor="mover",
                returns="veto",
                discipline="public",
                fires_from=(),
            )
            def at_pre_move(self, *args, **kwargs):
                pass

        qualname = _Demo2.at_pre_move.__qualname__
        self.assertIn(qualname, _REGISTRY)
        self.assertIs(_REGISTRY[qualname], _Demo2.at_pre_move.__evennia_hook__)

    def test_duplicate_qualname_raises(self):
        def _make():
            class _Dupe:
                @hook(
                    event="puppet",
                    phase="pre",
                    actor="target",
                    returns="veto",
                    discipline="public",
                    fires_from=(),
                )
                def at_pre_puppet(self, *a, **k):
                    pass

            return _Dupe

        _make()
        with self.assertRaises(ValueError):
            _make()

    def test_extends_marks_override(self):
        class _Override:
            @hook(extends="LifecycleMixin.at_pre_puppet", notes="suppress reattach")
            def at_pre_puppet(self, *a, **k):
                pass

        spec = _Override.at_pre_puppet.__evennia_hook__
        self.assertEqual(spec.extends, "LifecycleMixin.at_pre_puppet")
        self.assertEqual(spec.notes, "suppress reattach")
        # Override-only registrations omit event/phase/etc.
        self.assertIsNone(spec.event)


class LookupAPITest(EvenniaTestCase):
    """describe / for_event / list_all surface."""

    def setUp(self):
        super().setUp()
        self._saved_registry = dict(_REGISTRY)
        _reset_registry_for_tests()

        class _Look:
            @hook(
                event="puppet",
                phase="pre",
                actor="target",
                returns="veto",
                discipline="public",
                fires_from=(),
            )
            def at_pre_puppet(self, *a, **k):
                pass

            @hook(
                event="puppet",
                phase="post",
                actor="target",
                returns="ignored",
                discipline="public",
                fires_from=(),
            )
            def at_post_puppet(self, *a, **k):
                pass

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

        self.cls = _Look

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved_registry)
        super().tearDown()

    def test_describe_method(self):
        spec = describe(self.cls.at_pre_puppet)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.event, "puppet")

    def test_describe_unknown_returns_none(self):
        def not_a_hook():
            pass

        self.assertIsNone(describe(not_a_hook))

    def test_for_event_filters(self):
        puppets = for_event("puppet")
        events = {s.event for s in puppets}
        phases = {s.phase for s in puppets}
        self.assertEqual(events, {"puppet"})
        self.assertEqual(phases, {"pre", "post"})

    def test_list_all_enumerates(self):
        specs = list_all()
        self.assertEqual(len(specs), 3)


class PilotIntegrationTest(EvenniaTestCase):
    """End-to-end: the real LifecycleMixin.at_pre_puppet is registered."""

    def test_lifecycle_mixin_pre_puppet_registered(self):
        from evennia.objects.mixins.lifecycle import LifecycleMixin

        spec = describe(LifecycleMixin.at_pre_puppet)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.event, "puppet")
        self.assertEqual(spec.phase, "pre")
        self.assertEqual(spec.actor, "target")
        self.assertEqual(spec.returns, "veto")
        self.assertIn("DefaultAccount.puppet_object", spec.fires_from)

    def test_live_registry_survives_isolation_resets(self):
        # Sibling test classes save+restore _REGISTRY. The real
        # LifecycleMixin entry must still be reachable via for_event.
        events = for_event("puppet")
        qualnames = {id(spec) for spec in events}
        from evennia.objects.mixins.lifecycle import LifecycleMixin

        self.assertIn(id(LifecycleMixin.at_pre_puppet.__evennia_hook__), qualnames)
