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


@dataclass(frozen=True, slots=True)
class CharacterListWebDTO:
    """Plain character row for stock list and management templates."""

    id: int
    key: str
    detail_url: str
    update_url: str
    delete_url: str
    puppet_url: str
    location_key: str
    has_account: bool
    date_created: str
    subtitle: str
    description: str

    def __str__(self):
        """Return the display key used by stock templates."""
        return self.key


@dataclass(frozen=True, slots=True)
class CharacterMenuDTO:
    """Plain character identity used by the global account menu."""

    id: int
    key: str
    puppet_url: str

    def __str__(self):
        """Return the menu label."""
        return self.key


@dataclass(frozen=True, slots=True)
class CharacterMenuContextDTO:
    """Plain global-menu state for one authenticated account."""

    characters: tuple[CharacterMenuDTO, ...]
    puppet_name: str | None


@dataclass(frozen=True, slots=True)
class CharacterPageDTO:
    """Character list rows plus menu state resolved in the same IO call."""

    rows: tuple[CharacterListWebDTO, ...]
    menu: CharacterMenuContextDTO


@dataclass(frozen=True, slots=True)
class CharacterCreateResult:
    """Plain result of one stock character creation attempt."""

    created: bool
    key: str
    errors: tuple[str, ...]


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


def _owned_character(typeclass_path_value, object_id, slug, account_id, access_type):
    """Resolve one owned character and repeat slug/access checks."""
    obj = _object(typeclass_path_value, object_id)
    account = _account(account_id)
    _check_object_request(obj, account, slug, access_type)
    if not any(character and character.pk == obj.pk for character in account.characters):
        raise WebObjectPermissionDenied("Character is not owned by this account")
    return obj, account


def _character_list_dto(character):
    """Serialize one character row without leaking its typeclass or handlers."""
    location = getattr(character, "location", None)
    created = getattr(character, "db_date_created", None)
    return CharacterListWebDTO(
        id=int(character.id),
        key=str(character.key or ""),
        detail_url=str(character.web_get_detail_url()),
        update_url=str(character.web_get_update_url()),
        delete_url=str(character.web_get_delete_url()),
        puppet_url=str(character.web_get_puppet_url()),
        location_key=str(getattr(location, "key", "") or ""),
        has_account=bool(character.has_account),
        date_created=created.isoformat() if created else "",
        subtitle=str(getattr(character, "subtitle", "") or ""),
        description=str(getattr(character.db, "desc", "") or ""),
    )


def _character_menu_dto(character):
    """Serialize one owned character for the global menu."""
    return CharacterMenuDTO(
        id=int(character.id),
        key=str(character.key or ""),
        puppet_url=str(character.web_get_puppet_url()),
    )


def _owned_characters(account):
    """Return the account's live owned characters without null entries."""
    return tuple(character for character in account.characters if character)


def _menu_context(account, puppet_id):
    """Build bounded menu state from an already-loaded account."""
    owned = _owned_characters(account)
    menu_characters = tuple(_character_menu_dto(character) for character in owned[:10])
    try:
        puppet_id = int(puppet_id) if puppet_id is not None else None
    except (TypeError, ValueError):
        puppet_id = None
    puppet_name = next(
        (str(character.key or "") for character in owned if character.pk == puppet_id),
        None,
    )
    return CharacterMenuContextDTO(menu_characters, puppet_name)


def load_character_menu(account_id, puppet_id=None):
    """Serialize global character-menu state on the IO thread."""
    return _menu_context(_account(account_id), puppet_id)


def load_character_page(
    typeclass_path_value,
    account_id,
    access_type,
    owned_only,
    puppet_id=None,
):
    """Authorize and serialize a character collection plus its global menu."""
    typeclass = class_from_module(typeclass_path_value)
    account = _account(account_id)
    if owned_only:
        characters = tuple(
            character
            for character in _owned_characters(account)
            if character.db_typeclass_path == typeclass.path
        )
    else:
        characters = tuple(
            character
            for character in typeclass.objects.all().iterator()
            if character.access(account, access_type)
        )
    rows = tuple(
        sorted(
            (_character_list_dto(character) for character in characters),
            key=lambda row: row.key.casefold(),
        )
    )
    return CharacterPageDTO(rows=rows, menu=_menu_context(account, puppet_id))


def load_owned_character_detail(
    typeclass_path_value, object_id, slug, account_id, access_type, attribute_names
):
    """Authorize ownership and serialize one character on the IO thread."""
    obj, _account_obj = _owned_character(
        typeclass_path_value, object_id, slug, account_id, access_type
    )
    return _object_dto(obj, tuple(attribute_names))


def update_owned_character_attributes(
    typeclass_path_value,
    object_id,
    slug,
    account_id,
    access_type,
    attribute_values,
    attribute_names,
):
    """Authorize ownership and update character Attributes in one IO call."""
    obj, _account_obj = _owned_character(
        typeclass_path_value, object_id, slug, account_id, access_type
    )
    messages = []
    for key, value in attribute_values.items():
        obj.attributes.add(key, value)
        messages.append(f"Successfully updated '{key}' for {obj}.")
    return _object_dto(obj, tuple(attribute_names)), tuple(messages)


def authorize_character_puppet(typeclass_path_value, object_id, slug, account_id):
    """Authorize a stock website puppet selection and return plain identity."""
    obj, _account_obj = _owned_character(
        typeclass_path_value, object_id, slug, account_id, "puppet"
    )
    return _character_menu_dto(obj)


def delete_character(typeclass_path_value, object_id, slug, account_id, access_type):
    """Authorize ownership and delete one character in the same IO call."""
    obj, _account_obj = _owned_character(
        typeclass_path_value, object_id, slug, account_id, access_type
    )
    key = str(obj.key or "")
    obj.delete()
    return key


def create_character(typeclass_path_value, account_id, attribute_values):
    """Create one character and its Attributes entirely on the IO thread."""
    typeclass = class_from_module(typeclass_path_value)
    account = _account(account_id)
    values = dict(attribute_values)
    key = str(values.pop("db_key", ""))
    description = str(values.pop("desc", ""))
    character, errors = typeclass.create(key, account, description=description)
    errors = tuple(str(error) for error in (errors or ()))
    if character is None:
        return CharacterCreateResult(False, key, errors)
    for attribute, value in values.items():
        setattr(character.db, attribute, value)
    return CharacterCreateResult(True, str(character.key or key), errors)


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
