# Core Components

These are the 'building blocks' out of which Evennia is built. This documentation is complementary to, and often goes deeper than, the doc-strings of each component in the [API](../Evennia-API.md).

## Base components

These are base pieces used to make an Evennia game. Most are long-lived and are persisted in the database.

```{toctree} 
:maxdepth: 2
Portal-And-Server.md
Sessions.md
Typeclasses.md
Typeclass-Hooks.md
Typeclass-Hooks-Reference.md
Accounts.md
Objects.md
Characters.md
Rooms.md
Exits.md
Scripts.md
Channels.md
Msg.md
Attributes.md
Nicks.md
Tags.md
Prototypes.md
Help-System.md
Permissions.md
Locks.md
```

## Player input

Evennia's action engine handles player input. Legacy Command and CmdSet pages
remain during the compatibility retirement.

```{toctree} 
:maxdepth: 2

Default-Actions.md
Commands.md
Command-Sets.md
Default-Commands.md
Batch-Processors.md
Inputfuncs.md
```


## Utils and tools

Evennia provides a library of code resources to help the creation of a game.

```{toctree} 
:maxdepth: 2

Coding-Utils.md
EvEditor.md
EvForm.md
EvMore.md
EvTable.md
FuncParser.md
MonitorHandler.md
OnDemandHandler.md
TickerHandler.md
Signals.md
```

## Web components

Evennia is also its own webserver, with a website and in-browser webclient you can expand on.

```{toctree} 
:maxdepth: 2

Website.md
Webclient.md
Web-Admin.md
Webserver.md
Web-API.md
Web-Bootstrap-Framework.md
```
