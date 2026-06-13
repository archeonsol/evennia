# Testing

## Running Tests

Tests require a temporary game directory with migrations applied. Tests use Django's test runner, not pytest.

### Using `uv run` (preferred)

The `make` targets call bare `evennia`, which only works if evennia is installed in the active shell environment. With `uv run`, you must run the init/migrate/test steps yourself, and **run the test command from inside the game directory**:

```bash
# One-time setup (creates .test_game_dir/ and applies migrations):
uv run evennia --init .test_game_dir
cd .test_game_dir
uv run evennia migrate

# Run tests (from inside .test_game_dir/):
uv run evennia test --keepdb evennia.server.tests          # specific module
uv run evennia test --keepdb evennia                        # full suite
```

If `.test_game_dir/` already exists with migrations applied, skip straight to the test command.

### Always pipe test output to a temp file

The full suite is long-running and produces far more output than fits in a single tool result. If you run the test command bare, you only get the **tail** of the output, so the first failure scrolls out of reach and you have to rerun the whole suite to see it. On a large suite that is up to an hour of wasted wall-clock per rerun.

Pipe to a temp file with `tee` (which preserves live streaming) and inspect the file instead of rerunning:

```bash
uv run evennia test --keepdb evennia 2>&1 | tee /tmp/evennia-test.log
# then read the file to find failures, e.g.:
grep -nE 'FAIL|ERROR|Traceback' /tmp/evennia-test.log
```

Read the saved log for the failure details rather than re-executing the suite.

### Using `make` (requires `evennia` on PATH)

These targets handle init/migrate automatically but require `evennia` installed in the current environment (e.g., via `pip install -e .` or an activated virtualenv):

```bash
make test                                        # full suite
make testp                                       # parallel (4 cores)
make tests=evennia.objects.tests test             # specific module
make tests=evennia.commands.tests.test_command test  # specific test file
```

The Makefile creates `.test_game_dir/`, runs `evennia migrate`, then `evennia test --keepdb`.

## Test Base Classes

All in `evennia/utils/test_resources.py`:

- **`BaseEvenniaTest`** — sets up default objects (account, char1, char2, room1, room2, obj1, obj2, exit, script, session) with enforced default settings. Use this for testing Evennia library code.
- **`BaseEvenniaCommandTest`** — extends `BaseEvenniaTest`, adds `.call(CmdClass, input, expected_output)` for testing command execution.
- **`EvenniaTest`** / **`EvenniaCommandTest`** — same but uses game-dir settings/typeclasses (for downstream game tests, not Evennia library tests).
- **`EvenniaTestCase`** — lightweight, no default objects created. Faster for tests that don't need game state.

## Agent Tooling Tests

Tests for `.agents/tools/` use pytest (not Django's runner). Run with:

```bash
uv run pytest .agents/tools/tests/ -v
```

Do not use bare `python -m pytest` — the system Python may not have pytest installed. `uv run` ensures the project venv is used.

## Mocking guidance

Prefer `@patch.object(module_under_test, "name")` over `@patch("dotted.path.to.name")`.

Path-based patches break transparently when code relocates: the patch resolves a stale path and silently no-ops, but the test still passes against the un-patched code. Module-object patches follow the import, so they survive moves. Same rule for `mock.patch("module.delay")` where `delay` is imported into `module` from elsewhere — patch the imported reference, not the canonical path.

The `+underspire.16` contrib extraction surfaced 14 sites of the brittle pattern in one downstream-side test file. Engine tests likely carry similar at scale; tracked as backlog F17.

## CI Matrix

CI tests against SQLite, MySQL 8.0, and PostgreSQL 14 across Python 3.12/3.13/3.14. Coverage is collected on Python 3.12 + SQLite only.
