"""ASGI entrypoint for Evennia's Django application.

The counterpart to the Twisted-WSGI path in webserver.py. Serving Django over
ASGI (via uvicorn/hypercorn) unlocks Django 6 async views + async ORM and lets
HTTP and the shell websocket share one server. This module just exposes the ASGI
callable; wiring an ASGI server to it is a separate, gated seam so the default
serving stack is unchanged.

Assumes ``django.setup()`` has already run (as it has in server.py / portal.py by
the time this is imported), exactly like ``django.core.wsgi.get_wsgi_application``.
"""

from django.conf import settings
from django.core.asgi import get_asgi_application

application = get_asgi_application()

# The Twisted-WSGI path serves /static and /media via Twisted static resources.
# Under ASGI, Django serves them only if we wrap the app. In DEBUG we use the
# staticfiles handler; production should front static with a real server/CDN.
if getattr(settings, "DEBUG", False):
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

    application = ASGIStaticFilesHandler(application)
