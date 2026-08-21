"""Coercing JSON input into the exact types the mutation service demands.

A browser sends JSON, which has four scalar types. The mutation validator
compares with ``type(value) not in types``, not ``isinstance`` -- deliberately,
because a bool is not an int there and a subclass is not its parent. Something
has to bridge that, and doing it at the call site would mean every panel
reimplementing datetime parsing slightly differently.

The rule this module follows: **coerce, or refuse with a sentence.** Never
guess, never silently drop a field, and never pass a value through hoping the
validator will take it. A rejected value must tell the operator which field it
was and what was wrong, because that message is the only thing standing between
them and a form that will not submit for reasons nobody can see.

"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime, parse_time

from evennia.console.services import freeze_plain

#: Strings accepted as a true boolean, lowercased.
TRUE_WORDS = frozenset({"1", "true", "yes", "on", "t", "y"})

#: Strings accepted as a false boolean, lowercased.
FALSE_WORDS = frozenset({"0", "false", "no", "off", "f", "n", ""})


class CoercionError(ValueError):
    """One field's value cannot become the type the service requires."""

    def __init__(self, field: str, message: str):
        """Record which field failed, and why.

        Args:
            field: The field name.
            message: What was wrong with the value.
        """

        self.field = field
        super().__init__(f"{field}: {message}")


def _to_bool(field, value):
    """Coerce one value to ``bool``."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise CoercionError(field, f"{value!r} is not true or false")


def _to_int(field, value):
    """Coerce one value to ``int``."""

    if isinstance(value, bool):
        # bool is a subclass of int, and the validator compares exact types.
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise CoercionError(field, f"{value!r} is not a whole number") from None


def _to_float(field, value):
    """Coerce one value to ``float``."""

    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        raise CoercionError(field, f"{value!r} is not a number") from None


def _to_decimal(field, value):
    """Coerce one value to ``Decimal``."""

    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise CoercionError(field, f"{value!r} is not a decimal number") from None


def _to_datetime(field, value):
    """Coerce one value to an aware ``datetime``.

    Naive input is interpreted in the configured time zone rather than
    rejected: an operator typing a timestamp means the server's wall clock,
    and refusing it would teach them to paste offsets they do not have.
    """

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value).strip())
    if parsed is None:
        raise CoercionError(field, f"{value!r} is not a date and time")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _to_date(field, value):
    """Coerce one value to ``date``."""

    parsed = value if isinstance(value, date) and not isinstance(value, datetime) else None
    if parsed is None:
        parsed = parse_date(str(value).strip())
    if parsed is None:
        raise CoercionError(field, f"{value!r} is not a date")
    return parsed


def _to_time(field, value):
    """Coerce one value to ``time``."""

    parsed = value if isinstance(value, time) else parse_time(str(value).strip())
    if parsed is None:
        raise CoercionError(field, f"{value!r} is not a time of day")
    return parsed


def _to_uuid(field, value):
    """Coerce one value to ``UUID``."""

    try:
        return UUID(str(value).strip())
    except (TypeError, ValueError):
        raise CoercionError(field, f"{value!r} is not a UUID") from None


def _to_bytes(field, value):
    """Coerce one value to ``bytes``."""

    if isinstance(value, bytes):
        return value
    try:
        return str(value).encode("utf-8")
    except (TypeError, UnicodeEncodeError):
        raise CoercionError(field, "the value cannot be stored as bytes") from None


def _to_str(field, value):
    """Coerce one value to ``str``."""

    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


#: Coercion by target type name, in the order the service's declarations use.
_COERCERS = {
    "bool": _to_bool,
    "int": _to_int,
    "float": _to_float,
    "Decimal": _to_decimal,
    "datetime": _to_datetime,
    "date": _to_date,
    "time": _to_time,
    "UUID": _to_uuid,
    "bytes": _to_bytes,
    "str": _to_str,
}


def coerce_field(field: str, value, accepted: tuple[type, ...]):
    """Coerce one value into one of the types a field accepts.

    Args:
        field: Field name, used in the error message.
        value: The incoming JSON value.
        accepted: Exact types the mutation service will take.

    Returns:
        The value as an accepted type.

    Raises:
        CoercionError: The value cannot become any accepted type.
    """

    names = [item.__name__ for item in accepted]

    if value is None:
        if "NoneType" in names:
            return None
        raise CoercionError(field, "this field cannot be empty")

    # An exact hit needs no work. Checked first so a bool bound for a bool
    # field is never routed through the int coercer.
    for item in accepted:
        if type(value) is item:
            return value

    if "FrozenContainer" in names and isinstance(value, (dict, list, tuple, set)):
        return freeze_plain(value)

    # datetime before date: a datetime is a date, and the wrong order would
    # silently truncate the time.
    for name in ("datetime", "date", "time", "bool", "int", "float", "Decimal", "UUID", "bytes"):
        if name in names:
            return _COERCERS[name](field, value)
    if "str" in names:
        return _to_str(field, value)

    raise CoercionError(field, f"no supported type for this field ({', '.join(names)})")


def coerce_payload(payload, field_types) -> tuple[tuple[str, object], ...]:
    """Coerce a whole submitted payload into the service's concrete tuple.

    Args:
        payload: Field name to submitted value.
        field_types: Field name to accepted types, from the service.

    Returns:
        tuple: ``(name, value)`` pairs in sorted order, ready for the request.

    Raises:
        CoercionError: A field is unknown, or its value cannot be coerced.
    """

    concrete = []
    for name in sorted(payload):
        if name not in field_types:
            raise CoercionError(name, "this field cannot be written through the records lens")
        concrete.append((name, coerce_field(name, payload[name], field_types[name])))
    return tuple(concrete)
