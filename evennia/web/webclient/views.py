"""
This contains a simple view for rendering the webclient
page and serve it eventual static content.

"""

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.http import Http404
from django.shortcuts import render

from evennia.accounts.models import AccountDB
from evennia.utils import logger


def webclient(request):
    """
    Webclient page template loading.

    """
    # auto-login is now handled by evennia.web.utils.middleware

    # check if webclient should be enabled
    if not settings.WEBCLIENT_ENABLED:
        raise Http404

    # make sure to store the browser session's hash so the webclient can get to it!
    pagevars = {"browser_sessid": request.session.session_key}

    return render(request, "webclient.html", pagevars)


def webclient2(request):
    """The Svelte shell client (dual-route while it reaches parity).

    Serves the new default web client under a separate URL so the legacy Golden
    Layout client stays the default until this hits feature parity. Same
    session-hash wiring; the page loads evennia.js then the built shell bundle.
    """
    if not settings.WEBCLIENT_ENABLED:
        raise Http404

    pagevars = {"browser_sessid": request.session.session_key}
    return render(request, "webclient/client2.html", pagevars)
