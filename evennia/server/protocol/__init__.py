"""Azaban OOB event contract: a registry of typed server->shell events.

The engine owns the *mechanism* (registry, validation, TS codegen) and registers
its own core events (session lifecycle, media, the UI primitive). Games register
their own events via ``settings.PROTOCOL_EVENT_MODULES`` (same pattern as
``INPUT_FUNC_MODULES``). The generator walks the merged registry, so client and
server share one source of truth.

Register from an event module::

    from evennia.server.protocol import register_event
    register_event("ticket_msg", fields={"id": "str", "text": "str", "html?": "str"})

Field types: str, int, float, bool, list, dict, any. Suffix "?" = optional.
Carrier: "kwargs" (payload is the kwargs dict) or "args" (payload is args[0]);
an args event uses field name "_" for a single value or "_map" for a dict.
"""

from __future__ import annotations

from typing import Any, Optional

TYPES = {"str", "int", "float", "bool", "list", "dict", "any"}
_PY = {"str": str, "int": int, "float": float, "bool": bool, "list": list, "dict": dict, "any": Any}

_EVENTS: dict[str, dict] = {}
_MODELS: dict[str, Any] = {}
_loaded = False


def register_event(name: str, carrier: str = "kwargs", fields: dict | None = None, doc: str = ""):
    """Register (or replace) an OOB event in the catalog."""
    if carrier not in ("kwargs", "args"):
        raise ValueError(f"event {name!r}: carrier must be 'kwargs' or 'args'")
    fields = fields or {"_": "any"}
    for fname, ftype in fields.items():
        if ftype not in TYPES:
            raise ValueError(f"event {name!r}: bad field type {ftype!r} for {fname!r}")
    _EVENTS[name] = {"carrier": carrier, "fields": dict(fields), "doc": doc}
    _MODELS.pop(name, None)  # invalidate cached model


def load_event_modules():
    """Import the engine baselines and every ``settings.PROTOCOL_EVENT_MODULES``.

    The catalog decides which commands the server may send, so a module that
    fails to import raises instead of leaving its names undeclared. The server
    calls this at start; a failed load is retried on the next call.

    Raises:
        Exception: Whatever the failing module raised on import.
    """
    global _loaded
    if _loaded:
        return
    from importlib import import_module

    from django.conf import settings

    # engine-shipped baselines (events + outputfunc signatures)
    import evennia.server.protocol.core_events  # noqa: F401
    import evennia.server.protocol.core_outputfuncs  # noqa: F401

    for path in getattr(settings, "PROTOCOL_EVENT_MODULES", []) or []:
        import_module(path)
    _loaded = True


def all_events() -> dict[str, dict]:
    """The merged catalog (loads PROTOCOL_EVENT_MODULES on first call)."""
    load_event_modules()
    return dict(_EVENTS)


def is_client_command(name: str) -> bool:
    """Whether ``name`` is declared as a server->client command.

    The server sends only declared commands. A name is declared when it is a
    registered event or a registered outputfunc.

    Args:
        name (str): A command name, the key of one ``msg`` keyword.

    Returns:
        bool: True if a session may receive ``name``.
    """
    load_event_modules()
    from evennia.server.protocol.outputfuncs import is_outputfunc

    return name in _EVENTS or is_outputfunc(name)


def _pascal(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def model_for(event: str):
    """Pydantic model for a kwargs event with named fields, or None."""
    load_event_modules()
    if event in _MODELS:
        return _MODELS[event]
    spec = _EVENTS.get(event)
    if not spec or spec["carrier"] != "kwargs":
        return None
    fields = spec["fields"]
    if "_" in fields or "_map" in fields:
        return None
    from pydantic import ConfigDict, create_model

    py_fields = {}
    for fname, ftype in fields.items():
        optional = fname.endswith("?")
        key = fname[:-1] if optional else fname
        t = _PY[ftype]
        py_fields[key] = (Optional[t], None) if optional else (t, ...)
    model = create_model(
        f"{_pascal(event)}Payload", __config__=ConfigDict(extra="allow"), **py_fields
    )
    _MODELS[event] = model
    return model


def validate(event: str, payload: dict) -> tuple[bool, Optional[str]]:
    """Validate a kwargs payload against the catalog. (ok, error_message)."""
    model = model_for(event)
    if model is None:
        return True, None
    try:
        model(**(payload or {}))
        return True, None
    except Exception as err:
        return False, str(err)
