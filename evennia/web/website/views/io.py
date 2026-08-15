"""IO-owned services and plain view models for the stock website."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass

from django.contrib.auth.models import AnonymousUser
from django.core.serializers.json import DjangoJSONEncoder
from django.urls import reverse
from django.utils.text import slugify

from evennia.utils import class_from_module


class WebObjectNotFound(LookupError):
    """The requested web object no longer exists."""


class WebObjectSlugMismatch(LookupError):
    """The supplied slug does not identify the requested object."""


class WebObjectPermissionDenied(PermissionError):
    """The requesting account may not perform the requested operation."""


@dataclass(frozen=True, slots=True)
class ObjectWebDTO:
    """Plain render data for an object detail or update page."""

    id: int
    key: str
    name: str
    dbref: str
    slug: str
    detail_url: str
    admin_url: str
    location_key: str
    typename: str
    has_account: bool
    description: str
    attributes: tuple[tuple[str, object], ...]

    def __str__(self):
        """Return the display key used by stock templates."""
        return self.key


@dataclass(frozen=True, slots=True)
class ChannelWebDTO:
    """Plain render data for a channel list or detail page."""

    id: int
    key: str
    name: str
    slug: str
    detail_url: str
    admin_url: str
    description: str
    subscription_count: int
    attributes: tuple[tuple[str, object], ...]
    log_filename: str = ""

    def __str__(self):
        """Return the display key used by stock templates."""
        return self.key


def _class_path(typeclass) -> str:
    """Return an import path for a typeclass class."""
    return f"{typeclass.__module__}.{typeclass.__qualname__}"


def typeclass_path(typeclass) -> str:
    """Return a stable typeclass import path for crossing the IO boundary."""
    return _class_path(typeclass)


def _account(account_id):
    """Resolve an authenticated account ID or an anonymous principal."""
    if account_id is None:
        return AnonymousUser()
    from evennia.accounts.models import AccountDB

    try:
        return AccountDB.objects.get(pk=int(account_id))
    except (AccountDB.DoesNotExist, TypeError, ValueError) as err:
        raise WebObjectPermissionDenied("Requesting account was not found") from err


def _object(typeclass_path_value, object_id):
    """Resolve one typeclassed object inside the IO context."""
    typeclass = class_from_module(typeclass_path_value)
    try:
        return typeclass.objects.get(pk=int(object_id))
    except (typeclass.DoesNotExist, TypeError, ValueError) as err:
        raise WebObjectNotFound("Object was not found") from err


def _check_object_request(obj, account, slug, access_type):
    """Validate slug and access against freshly loaded state."""
    if slugify(obj.name) != slug:
        raise WebObjectSlugMismatch("Object slug does not match")
    if not obj.access(account, access_type):
        raise WebObjectPermissionDenied("Object access denied")


def _object_dto(obj, attribute_names):
    """Serialize an object and requested Attribute values."""
    attributes = OrderedDict()
    for attribute in attribute_names:
        if attribute in obj._meta._property_names:
            value = getattr(obj, attribute, "")
        else:
            value = getattr(obj.db, attribute, "")
        attributes[attribute.title()] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
    description = str(getattr(obj.db, "desc", "") or "")
    location = getattr(obj, "location", None)
    return ObjectWebDTO(
        id=int(obj.id),
        key=str(obj.key or ""),
        name=str(obj.name or ""),
        dbref=str(obj.dbref),
        slug=slugify(obj.name),
        detail_url=str(obj.web_get_detail_url()),
        admin_url=str(obj.web_get_admin_url()),
        location_key=str(getattr(location, "key", "") or ""),
        typename=str(obj.typename or ""),
        has_account=bool(obj.has_account),
        description=description,
        attributes=tuple(attributes.items()),
    )


def load_object_detail(
    typeclass_path_value, object_id, slug, account_id, access_type, attribute_names
):
    """Authorize and serialize an object detail request on the IO thread."""
    obj = _object(typeclass_path_value, object_id)
    _check_object_request(obj, _account(account_id), slug, access_type)
    return _object_dto(obj, tuple(attribute_names))


def update_object_attributes(
    typeclass_path_value,
    object_id,
    slug,
    account_id,
    access_type,
    attribute_values,
    attribute_names,
):
    """Authorize, update Attributes, and serialize the result in one IO call."""
    obj = _object(typeclass_path_value, object_id)
    _check_object_request(obj, _account(account_id), slug, access_type)
    messages = []
    for key, value in attribute_values.items():
        obj.attributes.add(key, value)
        messages.append(f"Successfully updated '{key}' for {obj}.")
    return _object_dto(obj, tuple(attribute_names)), tuple(messages)


def _channel_dto(channel, *, include_log=False):
    """Serialize one channel without leaking its handlers or model."""
    return ChannelWebDTO(
        id=int(channel.id),
        key=str(channel.key or ""),
        name=str(channel.name or ""),
        slug=slugify(channel.db_key),
        detail_url=str(channel.web_get_detail_url()),
        admin_url=str(channel.web_get_admin_url()),
        description=str(getattr(channel.db, "desc", "") or ""),
        subscription_count=len(channel.subscriptions.all()),
        attributes=(("Name", str(channel.name or "")),),
        log_filename=str(channel.get_log_filename()) if include_log else "",
    )


def load_channel_list(typeclass_path_value, account_id):
    """Authorize and serialize all visible channels on the IO thread."""
    typeclass = class_from_module(typeclass_path_value)
    account = _account(account_id)
    rows = [
        _channel_dto(channel)
        for channel in typeclass.objects.all().iterator()
        if channel.access(account, "listen")
    ]
    return tuple(sorted(rows, key=lambda row: row.key.casefold()))


def load_channel_detail(typeclass_path_value, slug, account_id):
    """Authorize and serialize one channel on the IO thread."""
    typeclass = class_from_module(typeclass_path_value)
    account = _account(account_id)
    for channel in typeclass.objects.all().iterator():
        if slugify(channel.db_key) != slug:
            continue
        if not channel.access(account, "listen"):
            raise WebObjectPermissionDenied("Channel access denied")
        return _channel_dto(channel, include_log=True)
    raise WebObjectNotFound("Channel was not found")


def object_admin_change_url(object_id):
    """Return the stock object-admin change URL from a scalar ID."""
    return reverse("admin:objects_objectdb_change", args=[int(object_id)])
