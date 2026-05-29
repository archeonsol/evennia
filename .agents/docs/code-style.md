# Code Style

## Formatting

Do not manually format code. Run `make format` (black + isort) after editing. It handles line length (100 chars), indentation, and import sorting. Use `make lint` to check without modifying.

## Docstrings

All modules, classes, functions, and methods must have docstrings. Use Google-style with Markdown formatting.

Import order (isort handles this, but be aware): stdlib → Twisted → Django → `evennia` → `evennia.contrib`

### Command Docstrings

Command class docstrings double as in-game help text. They use a special format with **2-space indentation**:

```python
"""
Short header

Usage:
  key[/switches] <mandatory args> [optional]

Switches:
  switch1    - description
  switch2    - description

Examples:
  usage example and output

Longer documentation.

"""
```

- `[ ]` for optional args, `< >` for descriptions of what to type, `||` to separate choices
- Commands requiring arguments should return a `Usage:` message when called with no args

## Command Naming

Command keys follow a strict IC/OOC convention. The `@` prefix is **load-bearing**: `@open` and `open` are different commands. `CMD_IGNORE_PREFIXES` is gone, so the prefix can no longer be silently stripped at parse time. See [command-system.md](command-system.md) for the matching contract.

Rule:

- **No prefix** for character/IC actions: things the *character* is doing in the world. `look`, `get`, `drop`, `say`, `pose`, `whisper`, `inventory`, `home`, `setdesc`, `give`.
- **`@` prefix** for player/account/OOC actions: things the *player* is doing as themselves, outside of character roleplay. Admin (`@ban`, `@boot`, `@perm`), builder (`@open`, `@dig`, `@create`), system (`@reload`, `@shutdown`), account meta (`@charcreate`, `@quell`, `@option`, `@password`), batch processing (`@batchcommands`).

Test for which bucket a command belongs in: *am I, the player, doing this, or is my character doing this?* If you're toggling your own session settings, managing characters, or building rooms, you're acting as the player — prefix it `@`. If your character is interacting with the world or other characters, no prefix.

Carve-outs:

- **Chat channel commands stay unprefixed.** Named channels auto-generate their command from `channel.key` (e.g. `ooc`, `public`) and bypass the convention because typing `@ooc` constantly is hostile. The meta channel-manager command itself is `@channel`.
- Aliases follow the same rule as the key. If the key is `@perm`, all aliases must start with `@`.

Enforced by review, not CI.

## Settings reads

Read Django settings at the call site (`settings.X`), not via a
module-level snapshot (`_X = settings.X`). Snapshots capture at
import time, so `@override_settings` in tests becomes a silent no-op
and runtime reloads don't land. Django caches `settings` access
internally; the per-call cost is sub-microsecond.

```python
# bad — snapshotted at import
_IDLE_TIMEOUT = settings.IDLE_TIMEOUT
...
if now - session.cmd_last > _IDLE_TIMEOUT:

# good
if now - session.cmd_last > settings.IDLE_TIMEOUT:
```

Same rule for transforms and for default args (which also evaluate
at def-time):

```python
def wrap(text, width=None):
    if width is None:
        width = settings.CLIENT_DEFAULT_WIDTH
```

For non-trivial transforms, factor into a small helper that reads
on each call: `def _permission_hierarchy(): return [p.lower() for p in settings.PERMISSION_HIERARCHY]`.

**Carve-out: true boot constants** (`EVENNIA_DIR`, `GAME_DIR`,
`CACHE_DIR`, `SSL_CERTIFICATE_ISSUER`, `ENCODINGS`) are immutable
per-process and fine to snapshot. Narrow on purpose: when in doubt,
read at the call site.

Phase 1 of the engine cleanup (`+underspire.34`) swept the known
sites. No automated guard; catching this in review is enough.

### Function/Method Docstrings

Google-style with indented blocks:

```python
def funcname(a, b, d=False, **kwargs):
    """
    Brief description.

    Args:
        a (str): Description over
            multiple lines.
        b (int or str): Another argument.
        d (bool, optional): An optional keyword argument.

    Returns:
        str: The result.

    Raises:
        RuntimeException: If there is an error.

    Notes:
        Additional context.

    """
```
