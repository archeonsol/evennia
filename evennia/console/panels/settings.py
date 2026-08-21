"""Effective settings.

``settings_default.py`` is roughly 1,850 lines and a game overrides some
fraction of it. Today the only way to know the effective value of anything is
to import Django settings in ``@py``, which needs a running server and shell
access to the box.

Read-only. Runtime-editable configuration is ``ServerConfig``, which lives in
the Records lens; this panel reports what the process was started with.

**Masking is done here, in the service, not in a template.** A masked value
must never reach the browser, or devtools recovers it. The panel shows that a
value is masked rather than omitting the row, so an operator can tell "not
set" from "not shown" -- a distinction that matters when debugging why a
credential-dependent feature is off.

"""

from __future__ import annotations

import re

from django.conf import settings as django_settings

from evennia.console.registry import Panel

#: Name patterns whose values never leave the process. Substring matches on the
#: setting name, case-insensitive.
SECRET_PATTERNS = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "APIKEY",
    "PRIVATE_KEY",
    "SALT",
    "CREDENTIAL",
    "WEBHOOK",
    "DSN",
    "SENTRY",
)

#: Settings masked by exact name regardless of the patterns above.
SECRET_NAMES = frozenset(
    {
        "DATABASES",
        "CACHES",
        "EMAIL_HOST_PASSWORD",
        "SECRET_KEY",
        "SECRET_KEY_FALLBACKS",
    }
)

_URL_CREDENTIALS = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def is_secret(name: str) -> bool:
    """Return whether one setting's value must be masked.

    Args:
        name: The setting name.

    Returns:
        bool: True when the value must not leave the process.
    """

    upper = str(name).upper()
    if upper in SECRET_NAMES:
        return True
    return any(pattern in upper for pattern in SECRET_PATTERNS)


def _render(value, depth=0):
    """Render one setting value as bounded plain data."""

    if isinstance(value, (str, int, float, bool, type(None))):
        if isinstance(value, str):
            if _URL_CREDENTIALS.search(value):
                return "<masked: embedded credentials>"
            return value[:2000]
        return value
    if depth >= 3:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {str(key)[:200]: _render(item, depth + 1) for key, item in list(value.items())[:100]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_render(item, depth + 1) for item in list(value)[:200]]
    return str(value)[:2000]


class SettingsPanel(Panel):
    """Effective settings, with defaults, overrides, and masking."""

    key = "settings"
    label = "Settings"
    description = "Effective configuration, with overridden values marked."
    columns = ("name", "value", "overridden")
    needs_io = False

    def rows(self, ctx):
        """Return every public setting with its effective and default value.

        Returns:
            dict: Setting rows plus a count of how many differ from default.
        """

        try:
            from evennia import settings_default
        except Exception:  # noqa: BLE001 - report rather than fail the page
            settings_default = None

        rows = []
        overridden = 0
        for name in sorted(dir(django_settings)):
            if not name.isupper():
                continue
            try:
                value = getattr(django_settings, name)
            except Exception:  # noqa: BLE001
                continue
            secret = is_secret(name)
            has_default = settings_default is not None and hasattr(settings_default, name)
            default = getattr(settings_default, name, None) if has_default else None
            differs = has_default and default != value
            if differs:
                overridden += 1
            rows.append(
                {
                    "name": name,
                    "value": "<masked>" if secret else _render(value),
                    "default": (
                        "<masked>" if secret else (_render(default) if has_default else None)
                    ),
                    "masked": secret,
                    "has_default": has_default,
                    "overridden": bool(differs),
                    "source": "game" if differs or not has_default else "engine",
                }
            )
        return {
            "rows": rows,
            "total": len(rows),
            "overridden": overridden,
            "masked_note": (
                "Masked settings are shown as rows so 'not set' stays "
                "distinguishable from 'not shown'. Values never leave the server."
            ),
        }
