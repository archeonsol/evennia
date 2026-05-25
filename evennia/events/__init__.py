"""
Engine event bus for moderation audit, analytics, and cross-subsystem decoupling.

Usage::

    from evennia.events import emit

    emit("economy.transfer", {"amount": 50, "from_key": "a", "to_key": "b"}, actor=account)
"""

from evennia.events.bus import emit, subscribe

__all__ = ("emit", "subscribe")
