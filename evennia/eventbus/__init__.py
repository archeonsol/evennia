"""
Engine event bus for moderation audit, analytics, and cross-subsystem decoupling.

Not to be confused with :mod:`evennia.actions.events`, the in-process
``EventRegistry`` that backs ``@subscribe`` on action handlers. This module is
the durable, backend-configurable bus that writes ``GameEvent`` rows; that one
is a synchronous in-memory dispatcher. The two are unrelated.

Usage::

    from evennia.eventbus import emit

    emit("economy.transfer", {"amount": 50, "from_key": "a", "to_key": "b"}, actor=account)

"""

from evennia.eventbus.bus import emit, subscribe

__all__ = ("emit", "subscribe")
