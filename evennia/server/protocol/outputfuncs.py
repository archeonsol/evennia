"""Typed catalog of server->client outputfuncs (the data-plane frame commands).

Where :mod:`evennia.server.protocol` (events) types the high-level OOB events a
game emits, this catalog types the low-level *outputfuncs* that make up a
session output frame: ``text``, ``prompt``, ``options`` and friends. A frame is
``{outputfunc_name: [args, kwargs]}``; each entry can be validated against its
registered signature.

Validation is intentionally permissive (``extra="allow"`` on the kwargs model):
outputfuncs are an open protocol and portals/contribs add their own. The catalog
is the single typed description of the well-known ones, used for the TS codegen
and for opt-in frame validation (``settings.VALIDATE_OUTPUT_FRAMES``). It is not
enforced on the hot send path by default.
"""

from __future__ import annotations

from typing import Any, Optional

# name -> {"args": [type,...], "kwargs": {field: type}, "doc": str}
_OUTPUTFUNCS: dict[str, dict] = {}
_KW_MODELS: dict[str, Any] = {}

_TYPES = {"str", "int", "float", "bool", "list", "dict", "any"}
_PY = {"str": str, "int": int, "float": float, "bool": bool, "list": list, "dict": dict, "any": Any}


def register_outputfunc(
    name: str, args: list | None = None, kwargs: dict | None = None, doc: str = ""
):
    """Register (or replace) an outputfunc signature in the catalog.

    ``args`` is a list of positional type names; ``kwargs`` maps optional keyword
    field names (suffix ``?`` = optional, which all kwargs are by convention) to
    type names. Type names: str/int/float/bool/list/dict/any.
    """
    args = args or []
    kwargs = kwargs or {}
    for t in args:
        if t not in _TYPES:
            raise ValueError(f"outputfunc {name!r}: bad arg type {t!r}")
    for fname, ftype in kwargs.items():
        if ftype not in _TYPES:
            raise ValueError(f"outputfunc {name!r}: bad kwarg type {ftype!r} for {fname!r}")
    _OUTPUTFUNCS[name] = {"args": list(args), "kwargs": dict(kwargs), "doc": doc}
    _KW_MODELS.pop(name, None)


def all_outputfuncs() -> dict[str, dict]:
    return dict(_OUTPUTFUNCS)


def _kwargs_model(name: str):
    spec = _OUTPUTFUNCS.get(name)
    if not spec or not spec["kwargs"]:
        return None
    if name in _KW_MODELS:
        return _KW_MODELS[name]
    from pydantic import ConfigDict, create_model

    py_fields = {}
    for fname, ftype in spec["kwargs"].items():
        key = fname[:-1] if fname.endswith("?") else fname
        py_fields[key] = (Optional[_PY[ftype]], None)
    model = create_model(
        f"{''.join(p.capitalize() for p in name.split('_'))}Kwargs",
        __config__=ConfigDict(extra="allow"),
        **py_fields,
    )
    _KW_MODELS[name] = model
    return model


def validate_outputfunc(name: str, args: list, kwargs: dict) -> tuple[bool, Optional[str]]:
    """Validate one frame entry against its registered signature.

    Unknown outputfuncs pass (open protocol). Known ones check positional arity
    and keyword types (extra keywords allowed).
    """
    spec = _OUTPUTFUNCS.get(name)
    if spec is None:
        return True, None
    expected = spec["args"]
    if expected and len(args) > len(expected) and "any" not in expected[-1:]:
        return False, f"outputfunc {name!r}: too many positional args"
    model = _kwargs_model(name)
    if model is not None:
        try:
            model(**(kwargs or {}))
        except Exception as err:
            return False, str(err)
    return True, None


def validate_frame(frame: dict) -> tuple[bool, Optional[str]]:
    """Validate a whole output frame ``{name: [args, kwargs]}``."""
    for name, payload in (frame or {}).items():
        args, kwargs = [], {}
        if isinstance(payload, (list, tuple)) and len(payload) == 2:
            args, kwargs = payload[0] or [], payload[1] or {}
        ok, err = validate_outputfunc(name, list(args), dict(kwargs))
        if not ok:
            return False, err
    return True, None
