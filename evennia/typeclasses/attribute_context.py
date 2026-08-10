"""Private execution context shared by Attribute storage integrations."""

import contextvars
from contextlib import contextmanager


class ModelSaveScope:
    """Track whether one framework model save ultimately commits or rolls back."""

    __slots__ = ("status",)

    def __init__(self):
        self.status = "pending"

    def commit(self):
        """Mark the containing database transaction committed."""
        self.status = "committed"

    def rollback(self):
        """Mark the model save or its containing transaction rolled back."""
        self.status = "rolled_back"


_MODEL_SAVE_SCOPE = contextvars.ContextVar("attribute_model_save_scope", default=None)


def model_save_active():
    """Return whether an Evennia model save currently owns the DB operation."""
    return _MODEL_SAVE_SCOPE.get() is not None


def current_model_save_scope():
    """Return the active model-save outcome scope, if any."""
    return _MODEL_SAVE_SCOPE.get()


@contextmanager
def model_save_context():
    """Mark framework-owned model-save hooks for lock-order-safe initialization."""
    scope = ModelSaveScope()
    token = _MODEL_SAVE_SCOPE.set(scope)
    try:
        yield scope
    except Exception:
        scope.rollback()
        raise
    finally:
        _MODEL_SAVE_SCOPE.reset(token)
