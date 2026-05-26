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

Command keys follow a strict IC/OOC convention. The `@` prefix is **load-bearing**: post-Phase-3 (Phase 3 of the cmdset refactor, tracked in `CMDSET_REFACTOR.md`), `@open` and `open` are different commands. `CMD_IGNORE_PREFIXES` is gone, so the prefix can no longer be silently stripped at parse time.

Rule:

- **No prefix** for character/IC actions: things the *character* is doing in the world. `look`, `get`, `drop`, `say`, `pose`, `whisper`, `inventory`, `home`, `setdesc`, `give`.
- **`@` prefix** for player/account/OOC actions: things the *player* is doing as themselves, outside of character roleplay. Admin (`@ban`, `@boot`, `@perm`), builder (`@open`, `@dig`, `@create`), system (`@reload`, `@shutdown`), account meta (`@charcreate`, `@quell`, `@option`, `@password`), batch processing (`@batchcommands`).

Test for which bucket a command belongs in: *am I, the player, doing this, or is my character doing this?* If you're toggling your own session settings, managing characters, or building rooms, you're acting as the player — prefix it `@`. If your character is interacting with the world or other characters, no prefix.

Carve-outs:

- **Chat channel commands stay unprefixed.** Named channels auto-generate their command from `channel.key` (e.g. `ooc`, `public`) and bypass the convention because typing `@ooc` constantly is hostile. The meta channel-manager command itself is `@channel`.
- Aliases follow the same rule as the key. If the key is `@perm`, all aliases must start with `@`.

The audit script at `.agents/tools/cmdset_prefix_audit.py` walks every default cmdset and emits an inventory; CI runs it in `--check` mode against `PHASE3_AUDIT.md` so drift surfaces at PR time.

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
