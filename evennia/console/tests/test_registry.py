"""Tests for panel registration and the worker/IO-owner call boundary.

The dispatch tests are the load-bearing ones. The console's whole safety
argument is that a panel author cannot reach live game state from a worker
thread and cannot leak a live object out of an IO action, because the API does
not hand them the means. These assert that, rather than asserting that a
docstring says so.

"""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from evennia.console import registry as reg
from evennia.console.registry import (
    CONSOLE_ACCESS,
    CONSOLE_MODERATION,
    IOContext,
    Panel,
    PanelError,
    PanelRegistry,
    WorkerContext,
    dispatch,
    io_action,
    is_io_action,
)


def _ctx(*capabilities, **kwargs):
    """Build a worker context holding the named capabilities."""
    return WorkerContext(actor_id=1, capabilities=frozenset(capabilities), **kwargs)


class SamplePanel(Panel):
    """Panel exercising both sides of the boundary."""

    key = "sample"
    label = "Sample"
    columns = ("a", "b")

    def rows(self, ctx):
        return [{"a": 1, "b": "two"}]

    def context_type(self, ctx):
        return type(ctx).__name__

    @io_action
    def touch(self, ctx, value):
        return {"seen": value, "ctx": type(ctx).__name__}

    @io_action
    def leaks_a_model(self, ctx):
        from django.contrib.contenttypes.models import ContentType

        return ContentType

    def _private(self, ctx):
        return "unreachable"


class ModerationPanel(Panel):
    """Panel reachable by the moderation-only capability."""

    key = "moderation"
    label = "Moderation"
    moderation_only = True

    def rows(self, ctx):
        return []


class TestRegistration(SimpleTestCase):
    """Panels register, deduplicate, and fail closed on malformed input."""

    def setUp(self):
        self.registry = PanelRegistry()

    def test_registers_class_or_instance(self):
        self.registry.register(SamplePanel)
        self.assertIsInstance(self.registry.get("sample"), SamplePanel)
        self.registry.register(ModerationPanel())
        self.assertEqual(len(self.registry.panels()), 2)

    def test_panels_are_key_ordered(self):
        self.registry.register(SamplePanel)
        self.registry.register(ModerationPanel)
        self.assertEqual([panel.key for panel in self.registry.panels()], ["moderation", "sample"])

    def test_unknown_panel_fails_closed(self):
        with self.assertRaises(PanelError):
            self.registry.get("nope")

    def test_rejects_non_panel(self):
        with self.assertRaises(PanelError):
            self.registry.register(object())

    def test_rejects_keyless_and_labelless_panels(self):
        class NoKey(Panel):
            label = "x"

        class NoLabel(Panel):
            key = "k"

        with self.assertRaises(PanelError):
            self.registry.register(NoKey)
        with self.assertRaises(PanelError):
            self.registry.register(NoLabel)

    def test_conflicting_key_fails_closed(self):
        class Other(Panel):
            key = "sample"
            label = "Other"

        self.registry.register(SamplePanel)
        with self.assertRaises(PanelError):
            self.registry.register(Other)

    def test_describe_is_plain_data(self):
        panel = self.registry.register(SamplePanel)
        described = panel.describe()
        self.assertEqual(described["key"], "sample")
        self.assertEqual(described["columns"], ["a", "b"])
        self.assertFalse(described["moderation_only"])


class TestPanelModuleLoading(SimpleTestCase):
    """``CONSOLE_PANEL_MODULES`` behaves like ``SYSTEM_MODULES``."""

    def setUp(self):
        self.registry = PanelRegistry()

    @override_settings(CONSOLE_PANEL_MODULES=["evennia.console.tests.test_registry"])
    def test_module_without_hook_is_a_loud_error(self):
        # This test module deliberately has no register_panels().
        with self.assertRaises(PanelError):
            self.registry.load_modules()

    @override_settings(CONSOLE_PANEL_MODULES=[])
    def test_empty_configuration_is_fine(self):
        self.registry.load_modules()
        self.assertEqual(self.registry.panels(), ())


class TestAccess(SimpleTestCase):
    """One capability admits everything; the other admits moderation only."""

    def test_console_access_reaches_every_panel(self):
        ctx = _ctx(CONSOLE_ACCESS)
        self.assertTrue(SamplePanel().admits(ctx))
        self.assertTrue(ModerationPanel().admits(ctx))

    def test_moderation_capability_reaches_moderation_only(self):
        ctx = _ctx(CONSOLE_MODERATION)
        self.assertFalse(SamplePanel().admits(ctx))
        self.assertTrue(ModerationPanel().admits(ctx))

    def test_no_capability_reaches_nothing(self):
        ctx = _ctx()
        self.assertFalse(SamplePanel().admits(ctx))
        self.assertFalse(ModerationPanel().admits(ctx))

    def test_visible_filters_the_nav(self):
        registry = PanelRegistry()
        registry.register(SamplePanel)
        registry.register(ModerationPanel)
        self.assertEqual(
            [panel.key for panel in registry.visible(_ctx(CONSOLE_MODERATION))],
            ["moderation"],
        )
        self.assertEqual(len(registry.visible(_ctx(CONSOLE_ACCESS))), 2)


class TestDispatchBoundary(SimpleTestCase):
    """The worker/IO split is structural, not advisory."""

    def setUp(self):
        self.panel = SamplePanel()
        self.ctx = _ctx(CONSOLE_ACCESS)

    def test_io_action_marking(self):
        self.assertTrue(is_io_action(self.panel.touch))
        self.assertFalse(is_io_action(self.panel.rows))

    def test_worker_method_runs_inline_and_gets_a_worker_context(self):
        with patch.object(reg, "run_on_io_thread") as bridge:
            result = dispatch(self.panel, "context_type", self.ctx)
        self.assertEqual(result, "WorkerContext")
        bridge.assert_not_called()

    def test_io_action_crosses_the_bridge_with_an_io_context(self):
        with (
            patch.object(
                reg, "run_on_io_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)
            ) as bridge,
            patch.object(reg, "release_worker_db_connections") as release,
        ):
            result = dispatch(self.panel, "touch", self.ctx, 7)
        self.assertEqual(result["ctx"], "IOContext")
        self.assertEqual(result["seen"], 7)
        bridge.assert_called_once()
        release.assert_called_once()

    def test_connections_are_released_before_dispatch(self):
        order = []
        with (
            patch.object(
                reg, "release_worker_db_connections", side_effect=lambda: order.append("release")
            ),
            patch.object(
                reg,
                "run_on_io_thread",
                side_effect=lambda fn, *a, **kw: (order.append("bridge"), fn(*a, **kw))[1],
            ),
        ):
            dispatch(self.panel, "touch", self.ctx, 1)
        self.assertEqual(order, ["release", "bridge"])

    def test_a_leaked_model_is_rejected_at_the_boundary(self):
        with (
            patch.object(reg, "run_on_io_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)),
            patch.object(reg, "release_worker_db_connections"),
        ):
            with self.assertRaises(Exception):
                dispatch(self.panel, "leaks_a_model", self.ctx)

    def test_private_methods_are_unreachable(self):
        with self.assertRaises(PanelError):
            dispatch(self.panel, "_private", self.ctx)

    def test_missing_method_fails_closed(self):
        with self.assertRaises(PanelError):
            dispatch(self.panel, "nope", self.ctx)

    def test_dispatch_requires_a_worker_context(self):
        with self.assertRaises(PanelError):
            dispatch(self.panel, "rows", IOContext(actor_id=1, capabilities=frozenset()))

    def test_dispatch_enforces_panel_access(self):
        with self.assertRaises(PanelError):
            dispatch(self.panel, "rows", _ctx(CONSOLE_MODERATION))


class TestContexts(SimpleTestCase):
    """Contexts carry scalars and nothing reachable."""

    def test_worker_context_has_no_object_handles(self):
        ctx = _ctx(CONSOLE_ACCESS)
        for name in ("user", "request", "account", "session", "obj", "db"):
            self.assertFalse(hasattr(ctx, name), f"WorkerContext must not expose {name!r}")

    def test_contexts_are_frozen(self):
        ctx = _ctx(CONSOLE_ACCESS)
        with self.assertRaises(Exception):
            ctx.actor_id = 2

    def test_moderation_only_flag(self):
        self.assertTrue(_ctx(CONSOLE_MODERATION).moderation_only)
        self.assertFalse(_ctx(CONSOLE_ACCESS).moderation_only)
