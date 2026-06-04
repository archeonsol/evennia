"""
This sub-package defines the help system of Evennia. It is pretty
simple, mainly consisting of a database model to hold help entries.
The auto-cmd-help is rather handled by the default 'help' command
itself.

When ``ACTION_ENGINE_ENABLED`` is on, command help topics are supplied
by :mod:`evennia.help.catalog` instead of merged cmdsets.
"""
