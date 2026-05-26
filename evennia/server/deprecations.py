"""
Launcher-time settings sanity checks.

Originally a long list of breadcrumbs for pre-1.0 upstream renames
(CMDSET_DEFAULT, INLINEFUNC_*, TIME_*_PER_*, etc.) that Underspire
never set. Those were removed in `+underspire.19`; the file now hosts
only checks that still defend against real foot-guns on the current
schema, plus the dev-mode `check_warnings` prod-readiness signals.
"""

import os


def check_errors(settings):
    """
    Check for invalid settings shapes that should stop the launcher.

    Args:
        settings (Settings): The Django settings file

    Raises:
        DeprecationWarning if a critical invalid shape is found.

    """
    if settings.WEBSERVER_ENABLED and not isinstance(settings.WEBSERVER_PORTS[0], tuple):
        raise DeprecationWarning(
            "settings.WEBSERVER_PORTS must be on the form [(proxyport, serverport), ...]"
        )

    chan_connectinfo = settings.CHANNEL_CONNECTINFO
    if chan_connectinfo is not None and not isinstance(chan_connectinfo, dict):
        raise DeprecationWarning(
            "settings.CHANNEL_CONNECTINFO has changed. It "
            "must now be either None or a dict "
            "specifying the properties of the channel to create."
        )

    template_overrides_dir = os.path.join(settings.GAME_DIR, "web", "template_overrides")
    static_overrides_dir = os.path.join(settings.GAME_DIR, "web", "static_overrides")
    if os.path.exists(template_overrides_dir):
        raise DeprecationWarning(
            f"The template_overrides directory ({template_overrides_dir}) has changed name.\n"
            " - Rename your existing `template_overrides` folder to `templates` instead."
        )
    if os.path.exists(static_overrides_dir):
        raise DeprecationWarning(
            f"The static_overrides directory ({static_overrides_dir}) has changed name.\n"
            " 1. Delete any existing `web/static` folder and all its contents (this "
            "was auto-generated)\n"
            " 2. Rename your existing `static_overrides` folder to `static` instead."
        )

    if settings.MULTISESSION_MODE < 2 and settings.MAX_NR_SIMULTANEOUS_PUPPETS > 1:
        raise DeprecationWarning(
            f"settings.MULTISESSION_MODE={settings.MULTISESSION_MODE} is not compatible with "
            f"settings.MAX_NR_SIMULTANEOUS_PUPPETS={settings.MAX_NR_SIMULTANEOUS_PUPPETS}. "
            "To allow multiple simultaneous puppets, the multi-session mode must be higher than 1."
        )


def check_warnings(settings):
    """
    Check conditions and deprecations that should produce warnings but which
    does not stop launch.
    """
    if settings.DEBUG:
        print(" [Devel: settings.DEBUG is True. Important to turn off in production.]")
    if settings.IN_GAME_ERRORS:
        print(" [Devel: settings.IN_GAME_ERRORS is True. Turn off in production.]")
    if settings.ALLOWED_HOSTS == ["*"]:
        print(" [Devel: settings.ALLOWED_HOSTS set to '*' (all). Limit in production.]")
    if settings.SERVER_HOSTNAME == "localhost":
        print(
            " [Devel: settings.SERVER_HOSTNAME is set to 'localhost'. "
            "Update to the actual hostname in production.]"
        )

    for dbentry in settings.DATABASES.values():
        if "psycopg2" in dbentry.get("ENGINE", ""):
            print(
                "Deprecation: postgresql_psycopg2 backend is deprecated. "
                "Switch settings.DATABASES to use "
                '"ENGINE": "django.db.backends.postgresql" instead.'
            )
