"""
JSONB attribute document serialization.

JSON-safe values (int, float, bool, None, str, and recursively safe list/dict)
are stored verbatim. Everything else is pickle-encoded by Evennia's serializer
(which handles dbobject dbrefs and _Saver* proxies) and stored as a
sentinel-prefixed base64 string.

Sentinel: ``"__P:"`` — short, unambiguous, not a legal Python identifier.
"""

import base64
import logging

_SENTINEL = "__P:"
_logger = logging.getLogger(__name__)


def _is_json_safe(val) -> bool:
    """
    True iff *val* can be losslessly round-tripped through json.dumps/loads.

    Explicit type check rather than calling json.dumps so that subclasses
    (e.g. _SaverDict) and coercible types (e.g. tuple → list) are rejected.
    """
    t = type(val)
    if t in (bool, type(None), str):
        return True
    if t is int or t is float:
        return True
    if t is list:
        return all(_is_json_safe(v) for v in val)
    if t is dict:
        return all(isinstance(k, str) and _is_json_safe(v) for k, v in val.items())
    return False


def to_jsonb(value) -> object:
    """
    Encode *value* for storage in a JSONB document slot.

    Returns a JSON-serialisable object: either the value itself (if already
    JSON-safe) or ``"__P:<base64-pickle>"``.  Evennia's ``to_pickle`` is used
    for the pickle path so that dbobject references and _Saver* proxies are
    normalised correctly before pickling.
    """
    import pickle as _pickle

    from evennia.utils.dbserialize import to_pickle as _ep

    if _is_json_safe(value):
        return value
    # Normalise through Evennia's processor: strips _Saver* wrappers and
    # converts dbobjects.  The result may now be JSON-safe.
    try:
        normalized = _ep(value)
    except Exception:
        normalized = value
    if _is_json_safe(normalized):
        return normalized
    # Must sentinel-encode as pickle bytes.
    if __debug__:
        _logger.debug("to_jsonb: sentinel-encoding %r (type=%s)", type(value).__name__, type(normalized).__name__)
    try:
        raw_bytes = _pickle.dumps(normalized, protocol=5)
    except Exception:
        try:
            raw_bytes = _pickle.dumps(value, protocol=5)
        except Exception:
            _logger.warning("to_jsonb: unserializable value %r; storing null", type(value).__name__)
            return None  # unserializable; store null
    return _SENTINEL + base64.b64encode(raw_bytes).decode()


def from_jsonb(encoded, db_obj=None):
    """
    Decode a value from a JSONB document slot.

    Args:
        encoded: The stored value (plain JSON or sentinel string).
        db_obj: If given, containers are wrapped in _Saver* proxies so
            in-place mutations write back.  Pass the owning *attribute*
            object, not the TypedObject.

    Returns:
        The decoded Python value.
    """
    import pickle as _pickle

    from evennia.utils.dbserialize import from_pickle as _fp

    if isinstance(encoded, str) and encoded.startswith(_SENTINEL):
        raw_bytes = base64.b64decode(encoded[len(_SENTINEL):])
        normalized = _pickle.loads(raw_bytes)
        return _fp(normalized, db_obj=db_obj)
    # Plain JSON value; wrap containers in _Saver* for write-back if requested.
    if db_obj is not None and isinstance(encoded, (dict, list)):
        return _fp(encoded, db_obj=db_obj)
    return encoded
