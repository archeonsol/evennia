"""Formatting and typed lookup for native ``@examine``."""

from __future__ import annotations

from django.conf import settings

from evennia.authorization.storage import load_grants
from evennia.utils import funcparser, search, utils
from evennia.utils.ansi import raw as ansi_raw
from evennia.utils.dbserialize import deserialize
from evennia.utils.utils import crop, display_len, format_grid, inherits_from

__all__ = ["ExamineFormatter", "search_examine_target"]

_FUNCPARSER = None


def search_examine_target(caller, query, object_type, switches=()):
    """Search one of the four supported examine object tables."""
    if object_type == "object":
        return caller.search(query)
    if object_type == "account":
        try:
            return caller.search_account(query.lstrip("*"))
        except AttributeError:
            return caller.search(query.lstrip("*"), search_object="object" in switches)
    matches = getattr(search, f"search_{object_type}")(query)
    if not matches:
        caller.msg(f"No {object_type} found with key {query}.")
        return None
    if len(matches) > 1:
        caller.msg(
            f"Multiple {object_type} found with key {query}:\n"
            + ", ".join(f"{obj.key}(#{obj.id})" for obj in matches)
        )
        return None
    return matches[0]


class ExamineFormatter:
    """Render stable object/account/script/channel details without CmdSets."""

    detail_color = "|c"
    header_color = "|w"
    quell_color = "|r"
    separator = "-"

    @staticmethod
    def _safe_all(handler, **kwargs):
        if handler is None or not hasattr(handler, "all"):
            return []
        try:
            return handler.all(**kwargs)
        except (AttributeError, TypeError):
            return []

    def format_single_tag(self, tag):
        """Format a tag with an optional category."""
        return f"{tag.db_key}[{tag.db_category}]" if tag.db_category else str(tag.db_key)

    def attribute_value_type(self, value):
        """Return a useful type annotation for non-string attribute values."""
        if isinstance(value, str):
            return ""
        name = getattr(getattr(value, "__class__", None), "__name__", type(value).__name__)
        if str(name).startswith("_Saver"):
            try:
                value = deserialize(value)
            except Exception:  # noqa: BLE001 - display must survive malformed values
                pass
        return str(type(value))

    def format_single_attribute(self, attr, detail=False, obj=None):
        """Format one persistent/non-persistent attribute."""
        global _FUNCPARSER
        if _FUNCPARSER is None:
            _FUNCPARSER = funcparser.FuncParser(settings.FUNCPARSER_OUTGOING_MESSAGES_MODULES)
        key, category, value = attr.db_key, attr.db_category, attr.value
        strvalue = getattr(attr, "strvalue", None)
        value_suffix = ""
        if value is None and strvalue is not None:
            value, value_suffix = strvalue, " |B[strvalue]|n"
        value_type = self.attribute_value_type(value)
        type_suffix = f" |B[type:{value_type}]|n{value_suffix}" if value_type else value_suffix
        rendered = _FUNCPARSER.parse(ansi_raw(utils.to_str(value)), escape=True)
        if detail:
            return (
                f"Attribute {obj.name}/{self.header_color}{key}|n "
                f"[category={category}]{type_suffix}:\n\n{rendered}"
            )
        rendered = crop(rendered)
        category_suffix = f"[{category}]" if category else ""
        return f"{self.header_color}{key}|n{category_suffix}={rendered}{type_suffix}"

    def data(self, obj, object_type):
        """Collect the mature examine fields for one typed object."""
        data = {}
        name = getattr(obj, "name", getattr(obj, "key", str(obj)))
        data["Name/key"] = f"{name} ({getattr(obj, 'dbref', '')})"
        aliases = self._safe_all(getattr(obj, "aliases", None))
        if aliases:
            data["Aliases"] = ", ".join(utils.make_iter(str(obj.aliases)))
        if hasattr(obj, "typeclass_path"):
            data["Typeclass"] = (
                f"{getattr(obj, 'typename', type(obj).__name__)} ({obj.typeclass_path})"
            )
        sessions = self._safe_all(getattr(obj, "sessions", None))
        if sessions:
            data["Sessions"] = ", ".join(f"#{session.sessid}" for session in sessions)
        if getattr(obj, "email", None):
            data["Email"] = f"{self.detail_color}{obj.email}|n"
        if getattr(obj, "last_login", None):
            data["Last Login"] = f"{self.detail_color}{obj.last_login}|n"

        account = getattr(obj, "account", None)
        if account and inherits_from(obj, "evennia.objects.objects.DefaultObject"):
            data["Account"] = f"{self.detail_color}{account.name}|n ({account.dbref})"
            data["  Account Typeclass"] = f"{account.typename} ({account.typeclass_path})"
            grants = ", ".join(sorted(load_grants(account).by_capability)) or "<None>"
            if account.attributes.has("_quell"):
                grants += f" {self.quell_color}(suppressed)|n"
            data["  Account capability grants"] = grants

        for header, attr in (
            ("Location", "location"),
            ("Home", "home"),
            ("Destination", "destination"),
        ):
            target = getattr(obj, attr, None)
            if target:
                data[header] = f"{target.key} (#{target.id})"
        try:
            data["Capability grants"] = (
                ", ".join(sorted(load_grants(obj).by_capability)) or "<None>"
            )
        except Exception:  # noqa: BLE001 - inspection should remain available
            data["Capability grants"] = "<Unavailable>"
        policies = getattr(obj, "policies", None)
        policies = policies.all() if policies and hasattr(policies, "all") else {}
        data["Policy overrides"] = (
            utils.fill(
                "; ".join(f"{key}={policy.to_data()!r}" for key, policy in policies.items()),
                indent=2,
            )
            if policies
            else "Class defaults"
        )

        scripts = self._safe_all(getattr(obj, "scripts", None))
        if scripts:
            data["Scripts"] = str(obj.scripts)
        tags = sorted(
            self._safe_all(getattr(obj, "tags", None), return_objs=True),
            key=lambda tag: (tag.db_key, tag.db_category or ""),
        )
        if tags:
            data["Tags"] = utils.fill(
                ", ".join(self.format_single_tag(tag) for tag in tags), indent=2
            )
        attrs = self._safe_all(getattr(obj, "attributes", None))
        if attrs:
            data["Persistent Attributes"] = "\n  " + "\n  ".join(
                sorted(self.format_single_attribute(attr) for attr in attrs)
            )
        nattrs = self._safe_all(getattr(obj, "nattributes", None))
        if nattrs and nattrs[0]:
            data["Non-Persistent Attributes"] = "\n  " + "\n  ".join(
                sorted(self.format_single_attribute(attr) for attr in nattrs)
            )
        exits = getattr(obj, "exits", None)
        if exits:
            data["Exits"] = ", ".join(f"{exit.name}({exit.dbref})" for exit in exits)
        contents = getattr(obj, "contents", None)
        if contents:
            chars = [item for item in contents if getattr(item, "account", None)]
            things = [
                item
                for item in contents
                if not getattr(item, "account", None) and not getattr(item, "destination", None)
            ]
            if chars:
                data["Characters"] = ", ".join(f"{item.name}({item.dbref})" for item in chars)
            if things:
                data["Content"] = ", ".join(f"{item.name}({item.dbref})" for item in things)
        if object_type == "script":
            if getattr(obj, "db_desc", None):
                data["Description"] = crop(obj.db_desc, 20)
            if hasattr(obj, "db_persistent"):
                data["Persistent"] = "T" if obj.db_persistent else "F"
        if object_type == "channel" and hasattr(obj, "db_account_subscriptions"):
            account_subs = obj.db_account_subscriptions.all()
            object_subs = obj.db_object_subscriptions.all()
            data["Subscription Totals"] = (
                f"{account_subs.count() + object_subs.count()} "
                f"({len(obj.subscriptions.online())} online)"
            )
            if account_subs:
                data["Account Subscriptions"] = "\n  " + "\n  ".join(
                    format_grid(
                        [sub.key for sub in account_subs],
                        sep=" ",
                        width=settings.CLIENT_DEFAULT_WIDTH,
                    )
                )
            if object_subs:
                data["Object Subscriptions"] = "\n  " + "\n  ".join(
                    format_grid(
                        [sub.key for sub in object_subs],
                        sep=" ",
                        width=settings.CLIENT_DEFAULT_WIDTH,
                    )
                )
        return data

    def render(self, obj, object_type, width=None):
        """Render collected fields as the native examine page."""
        blocks = []
        max_width = 0
        for header, value in self.data(obj, object_type).items():
            if value is None:
                continue
            block = f"{self.header_color}{header}|n: {value}"
            blocks.append(block)
            max_width = max(max_width, *(display_len(line) for line in block.splitlines()))
        max_width = min(width or settings.CLIENT_DEFAULT_WIDTH, max_width)
        sep = self.separator * max_width
        return f"{sep}\n" + "\n".join(blocks) + f"\n{sep}"
