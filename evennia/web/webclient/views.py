"""
This contains a simple view for rendering the webclient
page and serve it eventual static content.

"""

import os

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.http import Http404
from django.shortcuts import render

from evennia.accounts.models import AccountDB
from evennia.utils import logger

#: Static assets whose mtime defines the client's cache-busting token. Anything
#: rebuilt alongside the shell belongs here.
_VERSIONED_ASSETS = ("webclient/shell/shell.js", "webclient/shell/shell.css")

_asset_version_cache = None


def _asset_version():
    """A cache-busting token that tracks the built client, not a hand-edited date.

    The shell bundle keeps a stable filename, so browsers hold a stale copy
    unless the URL changes. Deriving the token from the built files' mtimes means
    a rebuild invalidates caches on its own; a hand-pinned ``?v=`` in the template
    only works until the first person forgets to bump it, and then players are
    silently running an old client.

    Returns:
        str: the token, or ``"0"`` if the assets could not be located (in which
            case the caller is likely serving from a storage backend that
            fingerprints URLs itself, so no token is needed).

    """
    global _asset_version_cache
    if _asset_version_cache is not None:
        return _asset_version_cache
    stamp = 0
    try:
        from django.contrib.staticfiles import finders

        for asset in _VERSIONED_ASSETS:
            path = finders.find(asset)
            if path and os.path.exists(path):
                stamp = max(stamp, int(os.path.getmtime(path)))
    except Exception:
        logger.log_trace("webclient: could not stat static assets for cache-busting")
    _asset_version_cache = str(stamp)
    return _asset_version_cache


def webclient(request):
    """
    Serve the web client shell.

    Renders ``webclient.html``, which a game overrides to load the built shell
    bundle. Auto-login is handled by ``evennia.web.utils.middleware``; the
    browser session hash is passed through so the client can present it on the
    WebSocket, and ``asset_version`` busts the cache when the bundle is rebuilt.

    """
    if not settings.WEBCLIENT_ENABLED:
        raise Http404

    pagevars = {
        "browser_sessid": request.session.session_key,
        "asset_version": _asset_version(),
    }
    return render(request, "webclient.html", pagevars)
