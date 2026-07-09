"""Server-driven UI primitive (engine mechanism).

Push a declarative component (card / form / menu / table / gauge) to a player's
web shell; the shell renders it generically, no per-feature client code. Rides
the existing ``oob`` channel (``ui_component`` / ``ui_remove``). Interactions run
game commands (buttons/menus carry a ``cmd``; a form's ``cmd`` is a template with
``{field}`` placeholders).

Games call this; the shell renderer lives in the webclient. Spec shape::

    {
      "id": "vitals", "type": "card"|"form"|"menu"|"table"|"gauge",
      "title": "...", "body": "<html>",
      "buttons": [{"label","cmd"}], "options": [{"label","cmd"}],
      "fields": [{"name","label","type","placeholder","options"}], "cmd": "say {message}",
      "columns": [...], "rows": [[...]], "value": 7, "max": 10, "color": "#c9a44c",
      "dismissible": true, "dock": false,
    }
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from evennia.utils import logger

_WEBCLIENT_PROTOCOLS = frozenset({"websocket"})


# -- validation (co-located with the primitive) ---------------------------


class _UIButton(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    cmd: str


class _UIField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: Optional[str] = None
    type: Optional[str] = None
    placeholder: Optional[str] = None
    options: Optional[list[str]] = None


class UIComponent(BaseModel):
    """Validation for a UI component spec before it reaches the shell."""

    model_config = ConfigDict(extra="allow")
    id: str
    type: str
    title: Optional[str] = None
    body: Optional[str] = None
    buttons: Optional[list[_UIButton]] = None
    fields: Optional[list[_UIField]] = None
    cmd: Optional[str] = None
    submit_label: Optional[str] = None
    options: Optional[list[_UIButton]] = None
    columns: Optional[list[str]] = None
    rows: Optional[list[list[Any]]] = None
    value: Optional[float] = None
    max: Optional[float] = None
    color: Optional[str] = None
    dismissible: Optional[bool] = None
    dock: Optional[bool] = None


def validate_ui(comp: dict) -> tuple[bool, Optional[str]]:
    try:
        UIComponent(**(comp or {}))
        return True, None
    except Exception as err:
        return False, str(err)


# -- delivery -------------------------------------------------------------


def _sessions_of(target):
    if target is None:
        return []
    subs = getattr(target, "sessions", None)
    if subs is not None:
        try:
            sessions = list(subs.all())
        except TypeError:
            sessions = list(subs)
    else:
        sessions = [target]  # assume it's already a session
    return [s for s in sessions if getattr(s, "protocol_key", "") in _WEBCLIENT_PROTOCOLS]


def push(target, comp):
    """Show (or replace, by id) a UI component on the target's web shell."""
    if not isinstance(comp, dict) or not comp.get("id") or not comp.get("type"):
        return
    ok, err = validate_ui(comp)
    if not ok:
        logger.log_warn(f"ui.push: invalid component {comp.get('id')!r}: {err}")
    for s in _sessions_of(target):
        try:
            s.msg(ui_component=comp)
        except Exception:
            logger.log_trace("ui.push")


def remove(target, comp_id):
    """Remove a previously-pushed component by id."""
    if not comp_id:
        return
    for s in _sessions_of(target):
        try:
            s.msg(ui_remove={"id": comp_id})
        except Exception:
            logger.log_trace("ui.remove")


# -- convenience builders -------------------------------------------------


def card(target, comp_id, title, body="", buttons=None, dismissible=True):
    push(
        target,
        {
            "id": comp_id,
            "type": "card",
            "title": title,
            "body": body,
            "buttons": buttons or [],
            "dismissible": dismissible,
        },
    )


def menu(target, comp_id, title, options, dismissible=True):
    push(
        target,
        {
            "id": comp_id,
            "type": "menu",
            "title": title,
            "options": options,
            "dismissible": dismissible,
        },
    )


def form(target, comp_id, title, fields, cmd, submit_label="Submit", dismissible=True):
    push(
        target,
        {
            "id": comp_id,
            "type": "form",
            "title": title,
            "fields": fields,
            "cmd": cmd,
            "submit_label": submit_label,
            "dismissible": dismissible,
        },
    )


def gauge(target, comp_id, label, value, maximum, color="#c9a44c", dock=True):
    push(
        target,
        {
            "id": comp_id,
            "type": "gauge",
            "title": label,
            "value": value,
            "max": maximum,
            "color": color,
            "dock": dock,
            "dismissible": False,
        },
    )


def table(target, comp_id, title, columns, rows, dismissible=True):
    push(
        target,
        {
            "id": comp_id,
            "type": "table",
            "title": title,
            "columns": columns,
            "rows": rows,
            "dismissible": dismissible,
        },
    )
