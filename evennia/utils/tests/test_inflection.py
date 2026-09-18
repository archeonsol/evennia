"""Lazy inflection accessors, and the import-time guard they exist to keep."""

import ast
import pathlib

from django.test import TestCase

from evennia.utils.inflection import inflect_engine, pyinflect_module, warm

# Importing these costs ~8s (pyinflect drags in spacy). Any module-scope import
# of one of them lands on the critical path of django.setup(), so it is paid by
# every management command, migration check and test process.
LAZY_ONLY = {"inflect", "pyinflect"}

# The engine root, walked for the import guard below.
ENGINE_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _module_scope_imports(tree):
    """Yield top-level module names imported at module scope in `tree`."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module.split(".")[0]
        elif isinstance(node, ast.Try):
            # A guarded `try: import x / except ImportError:` is still a
            # module-scope import, and costs exactly the same.
            for child in node.body:
                yield from _module_scope_imports(ast.Module(body=[child], type_ignores=[]))


class LazyInflectionTest(TestCase):
    """The accessors work, and nothing re-adds the eager import they replaced."""

    def test_engine_is_usable_and_cached(self):
        engine = inflect_engine()

        self.assertIsNotNone(engine)
        self.assertIs(engine, inflect_engine())
        self.assertEqual(engine.an("hour"), "an hour")

    def test_pyinflect_is_usable_and_cached(self):
        module = pyinflect_module()

        self.assertIsNotNone(module)
        self.assertIs(module, pyinflect_module())
        self.assertEqual(module.getInflection("walk", tag="VBZ"), ("walks",))

    def test_no_engine_module_imports_inflect_at_module_scope(self):
        offenders = []
        for path in ENGINE_ROOT.rglob("*.py"):
            parts = path.relative_to(ENGINE_ROOT).parts
            if "tests" in parts or path.name.startswith("test_"):
                continue
            if path.name == "inflection.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            if LAZY_ONLY & set(_module_scope_imports(tree)):
                offenders.append(str(path.relative_to(ENGINE_ROOT)))

        self.assertEqual(
            offenders,
            [],
            "import inflect/pyinflect inside the function that needs them, or go "
            "through evennia.utils.inflection; a module-scope import puts ~8s "
            f"back on every django.setup(): {offenders}",
        )

    def test_warm_populates_both_caches(self):
        warm()

        self.assertIs(inflect_engine(), inflect_engine())
        self.assertIs(pyinflect_module(), pyinflect_module())

    def test_the_server_start_hook_warms_them(self):
        """The laziness is only safe because a live server pays up front.

        A server boots once and then serves players, so a first-use import
        would land inside whichever command happened to render prose first and
        block the IO thread for ~8s. Asserted against the source rather than by
        booting a server: the point is that the call site still exists.
        """
        service = pathlib.Path(__file__).resolve().parents[2] / "server" / "service.py"
        source = service.read_text(encoding="utf-8")
        start_hook = source.split("def at_server_start(self):", 1)[1].split("def ", 1)[0]

        self.assertIn("warm", start_hook, "at_server_start no longer warms the inflection cache")
