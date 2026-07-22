# Capability-only game migration

This guide covers the game-side changes required by the capability-only
authorization runtime, including the compatibility repairs shipped in
`6.0.0+underspire.174`. Engine and game should move together; do not deploy a
partially converted game against this engine.

For the authorization model itself, see [Capability Authorization](../Components/Authorization.md).

## Before changing code

Stop the Server and Portal, back up the database, and commit the current game
state. Inventory legacy authoring in the game repository:

```console
rg -n "\\b(locks|permissions)\\s*=|[\"'](locks|permissions)[\"']\\s*:|lockstring|\\.locks\\.|get_default_lockstring|@perm\\b"
```

Database lock columns remain temporarily for schema compatibility, but the
runtime never evaluates them. Every operation used by the game must have a
typed policy; missing operations fail closed.

## Register game capabilities

Put the game's namespaced vocabulary in a configured capability module:

```python
from evennia.authorization.capabilities import CapabilityDefinition


def register_capabilities(registry):
    """Register the game's authorization vocabulary."""

    registry.register(CapabilityDefinition("mygame.world.author"))
    registry.register(CapabilityDefinition("mygame.help.staff"))
    registry.register_bundle(
        "mygame_builders", ("mygame.world.author", "mygame.help.staff")
    )
```

Add its Python path to `AUTHORIZATION_CAPABILITY_MODULES`. Registration must
happen before a policy names the capability.

## Replace legacy declarations

| Legacy surface | Capability-only replacement |
|---|---|
| Command `locks = "cmd:..."` | `authorization = "public"`, `"disabled"`, or a registered capability |
| Typeclass `get_default_lockstring()` or `self.locks.add(...)` | Extend class `authorization_policies`, or call `self.policies.set(operation, policy)` |
| `create_object`, `create_script`, `create_account`, `create_channel`, or `create_help_entry` with `locks=` | Pass `policies={"edit": RequiresCapability(...)}` or another typed policy node |
| Attribute tuples or `AttributeProperty(..., lockstring=...)` | Remove the lockstring and protect the owning resource's `attrread`, `attrcreate`, or `attredit` policy |
| File-help dictionary `locks` or `permissions` | Optional `capability` key; it controls both listing and reading |
| `@lock` / `@perm` administration | `@policy`, `@scope`, and `@grant` |
| `@sethelp/locks` | `@sethelp/policy` |
| `clock channel=send:all()` | `clock channel=send=public` |

Policies are immutable nodes:

```python
from evennia.authorization.policy import Always, Never, RequiresCapability
from evennia.objects.objects import DefaultObject


class Workshop(DefaultObject):
    """An object whose editing is restricted to game authors."""

    authorization_policies = {
        **DefaultObject.authorization_policies,
        "craft": Always(),
        "edit": RequiresCapability("mygame.world.author"),
        "delete": Never(),
    }
```

Creation does not imply ownership. Grant authority explicitly and choose the
narrowest useful scope:

```python
from evennia.authorization.storage import grant_capability, resource_ref

grant_capability(
    f"account:{account.pk}",
    "mygame.world.author",
    scope_kind="resource",
    scope_key=resource_ref(workshop),
    provenance="game_setup",
)
```

World (`world:*`) and authored label scopes are available when resource scope
is too narrow. Django `is_superuser` controls Django administration only; it is
not game authority.

## Migrate channel settings

`CHANNEL_MUDINFO`, `CHANNEL_CONNECTINFO`, and each `DEFAULT_CHANNELS` item must
use `policies`, not `locks`. Settings must remain plain data, so serialize the
policy nodes:

```python
DEFAULT_CHANNELS = [
    {
        "key": "Public",
        "aliases": ("pub",),
        "desc": "Public discussion",
        "policies": {
            "control": {
                "schema": "auth.policy.v1",
                "type": "capability",
                "capability": "engine.channel.control",
            },
            "listen": {"schema": "auth.policy.v1", "type": "always"},
            "send": {"schema": "auth.policy.v1", "type": "always"},
        },
    }
]
```

`create_channel` accepts either typed Policy nodes or these serialized policy
mappings and validates the complete policy set before saving.

## Check operation-specific behavior

- Crafting checks the `craft` operation. `DefaultObject` makes it public; add a
  stricter typeclass or instance policy where needed.
- Container objects need an explicit public or restricted `get_from` policy.
- FuncParser `$search(access=...)` now names an authorization operation. The
  target needs that policy and the caller needs a matching scoped grant.
- File-help `capability` applies to both `read` and `view`.
- REST and website administrators need explicit engine/game grants. Logging in
  as a Django superuser does not bypass object, script, channel, or help policy.
- Game test doubles used as callers should implement `has_capability(...)`, and
  fixtures should grant the exact capability/scope under test.

If the game overrides `get_default_lockstring`, move those defaults into
`authorization_policies`. Structured appearance hooks are `at_look_node` and
`return_appearance_node`; existing `return_appearance` overrides remain bridged
during migration.

## Convert stored authority offline

With both processes stopped, use the bounded legacy importer for supported
historical rows and author explicit policies for anything custom. Then finalize:

```console
evennia auth_finalize_capabilities --apply --remove-authority-tags --clear-lock-storage
evennia auth_audit_capabilities
```

The audit must pass before startup. The importer is an offline conversion tool,
not a runtime compatibility evaluator.

## Verify the lock-step upgrade

From the game directory, run migrations, the engine suite, and the game suite:

```console
uv run evennia migrate
uv run evennia test --keepdb evennia
uv run evennia test --keepdb <game test labels>
```

Finally, create or reset a disposable game database and start it once. This
exercises file-help and default-channel creation paths that an established
database may skip because those records already exist.
