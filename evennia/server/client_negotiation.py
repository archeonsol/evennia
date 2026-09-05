"""Pure validation of retained client declarations, separate from actions."""


def capability_flags(name, kwargs):
    """Return validated flags, or None for an invalid or unrelated declaration."""
    if name in ("editor_client", "narrative_client"):
        supported = kwargs.get("supported", True)
        if not isinstance(supported, bool):
            return None
        key = "CLIENT_EDITOR" if name == "editor_client" else "CLIENT_NARRATIVE"
        return {key: supported}
    if name != "azaban_hello":
        return None
    caps = kwargs.get("caps", {})
    if not isinstance(caps, dict) or len(caps) > 32:
        return None
    clean = {}
    for key, value in caps.items():
        if not isinstance(key, str) or len(key) > 64:
            return None
        if key in ("rendersNodes", "rendersMarkup", "patches", "batching"):
            if not isinstance(value, bool):
                return None
        if not isinstance(value, (bool, str, int)) or (isinstance(value, str) and len(value) > 128):
            return None
        clean[key] = value
    return {"CLIENT_NARRATIVE": caps.get("rendersNodes", False), "AZABAN_CAPS": clean}


def retain_capabilities(session, frame):
    """Store current declarations without storing or executing any action."""
    changed = False
    for name, payload in frame.items():
        if not isinstance(payload, (list, tuple)) or len(payload) != 2:
            continue
        kwargs = payload[1]
        if not isinstance(kwargs, dict):
            continue
        flags = capability_flags(name, kwargs)
        if flags is not None:
            session.protocol_flags.update(flags)
            changed = True
    return changed
