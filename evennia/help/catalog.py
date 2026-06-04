"""
Access-aware help catalog for the action engine (CM1 Phase 8).

Stock Evennia command help filters topics through merged cmdsets and lock
checks. When ``ACTION_ENGINE_ENABLED`` is on, cmdsets are empty and verbs live
in the action registry instead — this module is the replacement: it builds
Command-shaped help entries from registered actions and filters them with the
same ``carry_out`` ``requires=`` predicates the dispatch engine uses (including
quell semantics via :class:`~evennia.actions.predicate.HasCapability`).

Player-facing help uses file/DB topics only (staff-authored). Staff may use
``help commands`` for an ACL-filtered command reference. Bare ``help`` lists
file/DB topics for everyone; it does not mix in action-registry verbs (those
are only under ``help commands``).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

__all__ = [
    "ActionHelpTopic",
    "actor_for_help",
    "actor_is_staff_for_help",
    "register_help_category_prefix",
    "resolve_help_category",
    "carry_out_specs_for",
    "action_help_accessible",
    "action_help_requirement_desc",
    "collect_action_help_topics",
    "lookup_action_help_topic",
    "action_help_index_entries",
    "should_include_action_topics_in_index",
    "is_help_commands_topic",
    "is_action_help_topic",
]

_CATEGORY_PREFIXES: list[tuple[str, str]] = []


def register_help_category_prefix(module_prefix: str, category: str) -> None:
    """Map action classes under ``module_prefix`` to a help index category."""
    _CATEGORY_PREFIXES.append((module_prefix, category))


def resolve_help_category(action_cls) -> str:
    """Category string for an action class (class attr → prefix map → setting)."""
    explicit = getattr(action_cls, "help_category", None)
    if explicit:
        return explicit
    mod = getattr(action_cls, "__module__", "") or ""
    for prefix, category in sorted(_CATEGORY_PREFIXES, key=lambda p: -len(p[0])):
        if mod.startswith(prefix):
            return category
    return getattr(settings, "DEFAULT_HELP_CATEGORY", "General")


def _action_auto_help_enabled(action_cls) -> bool:
    if hasattr(action_cls, "auto_help"):
        return bool(action_cls.auto_help)
    return bool(getattr(settings, "HELP_ACTIONS_DEFAULT_AUTO_HELP", False))


def actor_is_staff_for_help(actor) -> bool:
    """True when ``actor`` has effective staff rank (honors quell)."""
    from evennia.actions.permission import STAFF, resolve_capabilities

    obj = (
        getattr(actor, "effective", None)
        or getattr(actor, "character", None)
        or getattr(actor, "account", None)
    )
    if obj is None:
        return False
    try:
        return bool(resolve_capabilities(obj) & STAFF)
    except Exception:
        return False


def should_include_action_topics_in_index(actor) -> bool:
    """Whether bare ``help`` should list command topics for ``actor``."""
    if getattr(settings, "HELP_INDEX_ACTIONS", False):
        return True
    if getattr(settings, "HELP_INDEX_ACTIONS_FOR_STAFF", True):
        return actor_is_staff_for_help(actor)
    return False


def is_help_commands_topic(topic: str) -> bool:
    key = getattr(settings, "HELP_COMMANDS_TOPIC", "commands")
    return (topic or "").strip().lower() == str(key).strip().lower()


def actor_for_help(caller, session=None):
    """Build an :class:`~evennia.actions.actor.Actor` for help ACL checks."""
    from evennia.actions.actor import Actor

    if session is not None:
        account = getattr(session, "account", None) or caller
        puppet = session.get_puppet() if hasattr(session, "get_puppet") else None
        return Actor(
            session=session,
            account=account if hasattr(account, "permissions") else None,
            character=puppet,
            binding=getattr(session, "binding", None),
        )
    if hasattr(caller, "characters"):
        sess = None
        getter = getattr(getattr(caller, "sessions", None), "get", None)
        if callable(getter):
            got = getter()
            sess = got[0] if got else None
        return Actor(
            session=sess,
            account=caller,
            binding=getattr(sess, "binding", None) if sess else None,
            character=sess.get_puppet() if sess and hasattr(sess, "get_puppet") else None,
        )
    if hasattr(caller, "account"):
        account = getattr(caller, "account", None)
        sess = getattr(caller, "sessions", None)
        session_obj = None
        if sess is not None:
            getter = getattr(sess, "get", None)
            if callable(getter):
                got = getter()
                session_obj = got[0] if got else None
        return Actor(
            session=session_obj,
            account=account,
            character=caller,
            binding=getattr(session_obj, "binding", None) if session_obj else None,
        )
    return Actor(account=caller)


def _is_rule_provider_type(cls) -> bool:
    if not isinstance(cls, type):
        return False
    return getattr(cls, "__module__", "") not in ("types", "builtins")


def _provider_types(actor, action_cls) -> tuple[type, ...]:
    """Provider classes that may host ``carry_out`` rules for ``action_cls``."""
    handler = getattr(action_cls, "__primary_handler__", None)
    account = getattr(actor, "account", None)
    character = getattr(actor, "character", None) or getattr(actor, "effective", None)
    types: list[type] = []
    if handler is not None:
        if account is not None:
            try:
                if isinstance(account, handler):
                    ptype = type(account)
                    if _is_rule_provider_type(ptype):
                        types.append(ptype)
            except TypeError:
                pass
        if character is not None:
            try:
                if isinstance(character, handler):
                    ptype = type(character)
                    if _is_rule_provider_type(ptype):
                        types.append(ptype)
            except TypeError:
                pass
    else:
        if character is not None:
            ptype = type(character)
            if _is_rule_provider_type(ptype):
                types.append(ptype)
        if account is not None:
            ptype = type(account)
            if _is_rule_provider_type(ptype) and ptype not in types:
                types.append(ptype)
    if not types:
        eff = getattr(actor, "effective", None)
        if eff is not None:
            ptype = type(eff)
            if _is_rule_provider_type(ptype):
                types.append(ptype)
    return tuple(types)


def carry_out_specs_for(provider_type, action_cls):
    """All ``carry_out`` rule specs for ``action_cls`` on ``provider_type``'s MRO."""
    from evennia.actions.registry import rule_registry

    specs = []
    for cls in provider_type.__mro__:
        if cls is object:
            break
        specs.extend(rule_registry.rules_for(cls, action_cls, "carry_out"))
    return specs


def _dummy_action(action_cls):
    """Minimal action instance for ``requires=`` evaluation (no parse needed)."""
    inst = object.__new__(action_cls)
    if hasattr(inst, "block_reason"):
        inst.block_reason = 0
    return inst


def action_help_accessible(action_cls, actor, *, mode="list", staff_reference=False) -> bool:
    """True when help should expose this action to ``actor``.

    ``staff_reference=True`` includes every permitted verb (staff command index),
    ignoring ``auto_help``. Otherwise ``auto_help`` must be enabled on the class.
    """
    del mode
    if not staff_reference and not _action_auto_help_enabled(action_cls):
        return False
    verbs = getattr(action_cls, "__action_verbs__", ()) or ()
    if any(str(v).startswith("__") for v in verbs):
        return False

    dummy = _dummy_action(action_cls)
    memo: dict = {}
    any_spec = False
    for ptype in _provider_types(actor, action_cls):
        specs = carry_out_specs_for(ptype, action_cls)
        if not specs:
            continue
        any_spec = True
        if any(spec.requires is None for spec in specs):
            return True
        required = [spec for spec in specs if spec.requires is not None]
        if not required:
            return True
        if any(spec.requires.eval(dummy, actor, memo) for spec in required):
            return True
    return False if any_spec else False


def action_help_requirement_desc(action_cls, actor) -> str | None:
    """Human-readable requirement line for a topic page, or ``None``.

    Returns ``None`` when the actor may use the verb (open ``carry_out`` path,
    no gates, or at least one gated path passes). Only returns text when every
    ``carry_out`` rule with ``requires=`` failed for this actor.
    """
    dummy = _dummy_action(action_cls)
    memo: dict = {}
    parts = []
    seen = set()
    has_gated = False
    for ptype in _provider_types(actor, action_cls):
        for spec in carry_out_specs_for(ptype, action_cls):
            if spec.requires is None:
                return None
            has_gated = True
            if spec.requires.eval(dummy, actor, memo):
                return None
            desc = spec.requires.describe()
            if desc and desc not in seen:
                seen.add(desc)
                parts.append(desc)
    if not has_gated:
        return None
    if not parts:
        return "Requires staff privileges you do not currently have."
    return "Requires: " + "; ".join(parts)


@dataclass
class ActionHelpTopic:
    """Command-shaped help entry backed by an action registry verb."""

    key: str
    action_cls: type
    help_category: str
    aliases: list
    _doc: str
    auto_help: bool = True

    @property
    def auto_help_display_key(self):
        return self.key

    def access(self, caller, access_type="read", default=True, session=None):
        del access_type, default
        actor = actor_for_help(caller, session)
        return action_help_accessible(
            self.action_cls, actor, staff_reference=True
        )

    def get_help(self, caller, cmdset=None):
        del cmdset
        actor = actor_for_help(caller)
        req = action_help_requirement_desc(self.action_cls, actor)
        body = self._doc or f"No help text is defined for |w{self.key}|n."
        if req:
            body = f"|x{req}|n\n\n{body}"
        return body

    @property
    def search_index_entry(self):
        return {
            "key": self.key,
            "aliases": " ".join(self.aliases),
            "category": self.help_category,
            "tags": "",
            "text": self._doc,
        }


def _iter_action_verbs(action_registry=None):
    if action_registry is None:
        from evennia.actions import action_registry as action_registry
    for action_cls in action_registry.all_actions():
        verbs = getattr(action_cls, "__action_verbs__", ()) or ()
        doc = (action_cls.__doc__ or "").strip()
        category = resolve_help_category(action_cls)
        auto_help = _action_auto_help_enabled(action_cls)
        for verb in verbs:
            key = str(verb).strip().lower()
            if not key or key.startswith("__"):
                continue
            aliases = [v for v in verbs if v != verb]
            yield key, ActionHelpTopic(key, action_cls, category, aliases, doc, auto_help)


def collect_action_help_topics(
    actor, *, mode="list", staff_reference=False, action_registry=None
) -> dict:
    """Filtered ``{key: ActionHelpTopic}`` for ``actor``.

    Player-facing help omits actions unless ``HELP_INDEX_ACTIONS`` is enabled.
    Staff command reference passes ``staff_reference=True`` (``help commands``).
    """
    del mode
    if not staff_reference and not should_include_action_topics_in_index(actor):
        return {}
    acl_as_staff_ref = staff_reference or should_include_action_topics_in_index(actor)
    topics = {}
    seen = set()
    for key, topic in _iter_action_verbs(action_registry):
        if key in seen:
            continue
        seen.add(key)
        if action_help_accessible(
            topic.action_cls, actor, staff_reference=acl_as_staff_ref
        ):
            topics[key] = topic
    return topics


def lookup_action_help_topic(key: str, actor, *, include_denied=False, staff_reference=False):
    """Resolve a help key to ``(topic | None, denied: bool)``.

    Action topics are staff-only unless ``HELP_INDEX_ACTIONS`` is enabled.
    """
    if not staff_reference and not (
        getattr(settings, "HELP_INDEX_ACTIONS", False) or actor_is_staff_for_help(actor)
    ):
        return None, False
    key = (key or "").strip().lower()
    if not key:
        return None, False
    from evennia.actions import action_registry

    for topic_key, topic in _iter_action_verbs(action_registry):
        if topic_key == key or key in [a.lower() for a in topic.aliases]:
            if action_help_accessible(
                topic.action_cls, actor, staff_reference=True
            ):
                return topic, False
            if include_denied:
                return topic, True
            return None, False
    return None, False


def action_help_index_entries(action_registry=None) -> list[dict]:
    """Raw index rows for staff command search rebuild (optional)."""
    if not getattr(settings, "HELP_INDEX_ACTIONS_FOR_STAFF", True):
        return []
    entries = []
    seen = set()
    for key, topic in _iter_action_verbs(action_registry):
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "key": topic.key,
                "aliases": list(topic.aliases),
                "category": topic.help_category,
                "text": topic._doc,
                "action_cls": topic.action_cls,
            }
        )
    return entries


def is_action_help_topic(obj) -> bool:
    return isinstance(obj, ActionHelpTopic)
