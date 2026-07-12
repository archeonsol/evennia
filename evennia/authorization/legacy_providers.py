"""Registered contextual providers for safely compiled legacy lock functions."""

from __future__ import annotations

from .policy import register_predicate_provider


def _principal(context):
    """Return the effective object used by historical lock functions."""

    actor = context.principal
    return getattr(actor, "effective", None) or getattr(actor, "character", None) or actor


def _tag(context, params):
    """Evaluate an accessing-principal tag requirement."""

    obj = _principal(context)
    key = params.get("arg0", "")
    category = params.get("arg1")
    try:
        return bool(obj.tags.has(key, category=category))
    except AttributeError:
        return False


def _attr(context, params):
    """Evaluate an accessing-principal attribute requirement."""

    obj = _principal(context)
    key = params.get("arg0", "")
    expected = params.get("arg1")
    try:
        value = obj.attributes.get(key, default=None)
    except AttributeError:
        return False
    return value is not None if expected is None else str(value) == str(expected)


def _holds(context, params):
    """Return whether the principal contains the protected resource."""

    return context.resource in getattr(_principal(context), "contents", ())


def _inside(context, params):
    """Return whether the principal is directly inside the resource."""

    return getattr(_principal(context), "location", None) is context.resource


def _inside_recursive(context, params):
    """Return whether the principal is recursively inside the resource."""

    current = _principal(context)
    seen = set()
    while current is not None and id(current) not in seen:
        if current is context.resource:
            return True
        seen.add(id(current))
        current = getattr(current, "location", None)
    return False


def _self(context, params):
    """Return whether principal and protected resource are identical."""

    return _principal(context) is context.resource


def _has_account(context, params):
    """Return whether the principal is controlled by an account."""

    return getattr(_principal(context), "account", None) is not None


_CORE_LOCKFUNCS = {
    "attr",
    "objattr",
    "locattr",
    "objlocattr",
    "attr_eq",
    "attr_gt",
    "attr_ge",
    "attr_lt",
    "attr_le",
    "attr_ne",
    "tag",
    "objtag",
    "objloctag",
    "holds",
    "inside",
    "inside_rec",
    "self",
    "has_account",
    "is_ooc",
    "serversetting",
    "superuser",
}


def _core_lockfunc(context, params):
    """Invoke one audited core compatibility predicate without parsing text."""

    from evennia.locks import lockfuncs

    name = params.get("name", "")
    if name not in _CORE_LOCKFUNCS:
        return False
    args = []
    index = 0
    while f"arg{index}" in params:
        args.append(params[f"arg{index}"])
        index += 1
    kwargs = {
        key.removeprefix("kw_"): value for key, value in params.items() if key.startswith("kw_")
    }
    if context.session is not None:
        kwargs["session"] = context.session
    function = getattr(lockfuncs, name)
    return bool(function(context.principal, context.resource, *args, **kwargs))


register_predicate_provider("legacy.tag", _tag)
register_predicate_provider("legacy.attr", _attr)
register_predicate_provider("legacy.holds", _holds)
register_predicate_provider("legacy.inside", _inside)
register_predicate_provider("legacy.inside_rec", _inside_recursive)
register_predicate_provider("legacy.self", _self)
register_predicate_provider("legacy.has_account", _has_account)
register_predicate_provider("legacy.core_lockfunc", _core_lockfunc)
