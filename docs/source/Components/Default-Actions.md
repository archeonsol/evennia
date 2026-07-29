# Default actions

Player input is parsed into typed actions and handled by rules. The engine ships
generic defaults in `evennia.actions.default`; games compose the relevant rule
providers into their Character typeclass and add higher-priority game rules as
needed. Native defaults do not instantiate or adapt legacy `Command` classes.

The roleplay family exports `Say`, `Whisper`, `Pose`, `Emote`, and
`DefaultRoleplayRules`. `Whisper.targets` contains every resolved, deduplicated
local receiver, so target providers participate normally in dispatch. Speech
continues to use `at_pre_say`, `resolve_transform`, and `at_say`; pose and emote
use narrative delivery.

The building package exports `SetAttribute` (`Set`), `SetObjAlias` (`Alias`),
`Copy`, `CpAttr`, `Link`, `Unlink`, `SetHome`, `Wipe`, `Examine`, and
`CharacterBuildingRules`. These actions preserve typed searches and per-object
`control`/`edit`/`examine` policy checks. `@examine` reports object, account,
script, and channel state but deliberately has no stored, merged, or available
CmdSet section. `@cmdsets` has no native replacement because CmdSets are being
retired.

`CharacterSystemRules` also handles `Objects` (`@objects`) and `Scripts`
(`@scripts`, `@script`). Scripts are storage-only: listing, lookup, creation,
attachment, and deletion are supported; timer controls are not.

## Authorization

The new staff actions use account-scoped capabilities and still apply
object-level policies:

| Action family | Account capability |
| --- | --- |
| Building mutations | `engine.world.build` |
| `@examine` | `engine.object.examine` |
| `@objects` | `engine.system.inspect` |
| `@scripts` | `engine.script.control` |

Granting a capability only to a focused body does not authorize these actions,
and a quelled account grant remains suppressed.

## Imports

Roleplay and building names are lazy exports because common game action names
can collide during registration:

```python
from evennia.actions import CharacterBuildingRules, DefaultRoleplayRules
from evennia.actions.default import Examine, Say, Scripts
```

Importing action-core modules alone does not register the roleplay or building
verbs. A game should import the action families it installs and compose
`DefaultRoleplayRules`, `CharacterBuildingRules`, and `CharacterSystemRules`
into its Character provider.
