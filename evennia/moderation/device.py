"""
The device token: one browser's name for itself.

A first-party signed value, set as a cookie and mirrored by the page into
``localStorage`` and IndexedDB. Each store is rewritten from whichever copy
survived, so clearing one of the three restores it from the other two. That is
the whole mechanism -- there is no fingerprinting of the browser here, nothing
is derived from the machine, and a player who clears all three site stores is
a new device as far as this is concerned.

It exists because the Django session key is a weaker identity than it looks:
it rotates on login, it is dropped by a cache clear, and it is gone in a fresh
private window. An evader's second account is usually the same browser with a
new session, and that is exactly the case the session key cannot see.

The value is signed with the server's own key, so a token is either one this
server issued or it is discarded. It is not a credential and it authorizes
nothing: possession proves nothing beyond "this browser has been here", which
is all a moderation signal ever claims.
"""

from __future__ import annotations

import re
import secrets

from django.conf import settings
from django.core import signing

# The token is one column on SessionRecord, capped at 64 characters. The bare
# value is 32 hex characters; the signature rides in the cookie only.
_VALUE_BYTES = 16
_VALUE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_SALT = "evennia.moderation.device"

# Two years. A device token that expires within a season answers the question
# "is this the same browser as last week" and not the one worth asking.
COOKIE_MAX_AGE = 63_072_000


def cookie_name() -> str:
    """The cookie the token is stored in. Games may rename it."""
    return str(getattr(settings, "MODERATION_DEVICE_COOKIE", "device_id") or "device_id")


def _signer():
    return signing.Signer(salt=_SALT)


def mint() -> str:
    """A fresh signed token. The return value is what the browser stores."""
    return _signer().sign(secrets.token_hex(_VALUE_BYTES))


def verify(signed) -> str:
    """
    The bare value of a signed token, or an empty string.

    Empty covers every failure the same way: absent, malformed, signed by a
    different key, or carrying a value this server would never have issued.
    A rejected token is simply an unknown device, which is the default state.
    """
    text = str(signed or "").strip()
    if not text or len(text) > 256:
        return ""
    try:
        value = _signer().unsign(text)
    except signing.BadSignature:
        return ""
    return value if _VALUE_PATTERN.match(value) else ""


def _cookies_from_header(header) -> dict:
    """Parse a raw ``Cookie:`` header without importing Django's request stack.

    The portal sees websocket handshake headers, not an ``HttpRequest``, so
    ``request.COOKIES`` is not available where this is needed.
    """
    cookies = {}
    for part in str(header or "").split(";"):
        name, separator, value = part.partition("=")
        if not separator:
            continue
        cookies[name.strip()] = value.strip().strip('"')
    return cookies


def token_from_headers(headers) -> str:
    """The verified device token carried by one set of request headers."""
    try:
        mapping = headers or {}
        raw = mapping.get("cookie") or mapping.get("Cookie") or ""
    except Exception:
        return ""
    return verify(_cookies_from_header(raw).get(cookie_name(), ""))
