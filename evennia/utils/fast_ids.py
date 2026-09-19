"""Cheap process-unique identifiers for high-volume internal telemetry.

These identifiers correlate runtime work; they are not secrets or durable
database identities.  A random process prefix prevents practical collisions
across restarts, while a monotonic counter avoids an operating-system random
read and UUID object allocation for every rendered node or authorization
decision.
"""

from itertools import count
from uuid import uuid4

_PROCESS_PREFIX = uuid4().hex[:16]
_SEQUENCE = count()
_MAX_SEQUENCE = (1 << 64) - 1


def new_runtime_id() -> str:
    """Return a 32-character hexadecimal identifier unique in this process."""
    sequence = next(_SEQUENCE)
    if sequence > _MAX_SEQUENCE:
        raise RuntimeError("runtime identifier sequence exhausted")
    return f"{_PROCESS_PREFIX}{sequence:016x}"
