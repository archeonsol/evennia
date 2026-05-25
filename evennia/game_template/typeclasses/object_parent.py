"""
ObjectParent

A pure-Python mixin shared by all in-world entities (Objects, Characters,
Rooms, Exits). Kept in its own module (with no model imports) so that
Django migrations can reference it without triggering proxy-model
registration.

"""


class ObjectParent:
    """
    This is a mixin that can be used to override *all* entities inheriting at
    some distance from DefaultObject (Objects, Exits, Characters and Rooms).

    Just add any method that exists on `DefaultObject` to this class. If one
    of the derived classes has itself defined that same hook already, that
    will take precedence.

    """
