"""The console's built assets, and whether they are actually there.

C9 says a game must never need ``npm`` to run the console, so the build output
is committed. The plan's own risk section names the cost of that: a committed
build artifact rots.

These are the checks that catch the two ways it rots visibly. Neither can tell
whether the bundle is *current* -- only rebuilding can do that -- but both catch
the failure that produces a blank page on a deployment nobody can debug from the
server logs, because the server is serving exactly what it was asked to.

"""

import re
from pathlib import Path

from django.test import SimpleTestCase

import evennia

STATIC = Path(evennia.__file__).parent / "web" / "static" / "console"
TEMPLATE = Path(evennia.__file__).parent / "web" / "templates" / "console" / "index.html"
CLIENT = Path(evennia.__file__).parent / "web" / "console" / "client"


class TestBuiltAssetsArePresent(SimpleTestCase):
    """Every asset the template asks for exists in the static tree."""

    def _referenced(self):
        """Return the static paths the console template loads."""

        markup = TEMPLATE.read_text(encoding="utf-8")
        return re.findall(r"{%\s*static\s+'([^']+)'\s*%}", markup)

    def test_the_template_is_there(self):
        self.assertTrue(TEMPLATE.is_file(), f"{TEMPLATE} is missing")

    def test_the_template_asks_for_something(self):
        # A template that references nothing would pass every check below while
        # rendering an empty page.
        self.assertTrue(self._referenced(), "the console template loads no assets")

    def test_every_referenced_asset_exists(self):
        root = Path(evennia.__file__).parent / "web" / "static"
        missing = [name for name in self._referenced() if not (root / name).is_file()]
        self.assertEqual(
            missing,
            [],
            "The console template loads these files and they are not in the static tree. "
            "Run `npm run build` in evennia/web/console/client and commit the output.",
        )

    def test_the_bundle_is_not_empty(self):
        bundle = STATIC / "app" / "console-app.js"
        self.assertTrue(bundle.is_file(), f"{bundle} is missing")
        # A zero-length or near-empty bundle is what a failed build leaves
        # behind, and it serves with a 200.
        self.assertGreater(bundle.stat().st_size, 10_000)

    def test_component_styles_are_loaded(self):
        self.assertIn("console/app/console-app.css", self._referenced())

    def test_the_source_that_builds_it_is_committed_too(self):
        # Shipping the artifact without its source is how it becomes
        # unmaintainable rather than merely stale.
        self.assertTrue((CLIENT / "package.json").is_file())
        self.assertTrue((CLIENT / "src" / "main.ts").is_file())


class TestTheVanillaClientIsGone(SimpleTestCase):
    """The client this replaced must not still be served.

    Two clients in the static tree is how a fix lands in one of them.
    """

    def test_the_old_script_is_removed(self):
        self.assertFalse(
            (STATIC / "console.js").exists(),
            "console.js was replaced by the Svelte client and must not be served.",
        )

    def test_the_stylesheets_are_kept(self):
        # The port replaced the logic, not the design. These are the visual
        # world, and mission.css is shared with the game's own staff pages.
        self.assertTrue((STATIC / "console.css").is_file())
        self.assertTrue((STATIC / "mission.css").is_file())
