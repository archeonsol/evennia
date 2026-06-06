"""Help rendering for the action engine (CM1 Phase 8)."""

from __future__ import annotations

from collections import defaultdict

from evennia.help.utils import parse_entry_for_subcategories
from evennia.utils.utils import inherits_from

__all__ = ["render_help"]


def _topic_in_file_db(file_db_help_topics, query: str) -> bool:
    """True when ``query`` matches a file/DB help key or alias."""
    key = (query or "").strip().lower()
    if not key:
        return False
    for topic_key, entry in file_db_help_topics.items():
        if topic_key == key:
            return True
        aliases = entry.aliases
        if not isinstance(aliases, list):
            try:
                aliases = list(aliases.all())
            except Exception:
                aliases = list(aliases or [])
        if key in [str(a).lower() for a in aliases]:
            return True
    return False


def _format_bare_index(helper, caller, actor, cmd_help_topics, file_db_help_topics):
    cmd_help_by_category = defaultdict(list)
    file_db_help_by_category = defaultdict(list)
    for key, cmd in cmd_help_topics.items():
        cmd_help_by_category[cmd.help_category].append(key)
    for key, entry in file_db_help_topics.items():
        file_db_help_by_category[entry.help_category].append(key)
    return helper.format_help_index(
        cmd_help_by_category,
        file_db_help_by_category,
        click_topics=helper.clickable_topics,
    )


def render_help(caller, topic: str = "", subtopics=None, cmdset=None, session=None):
    """Render help using :class:`~evennia.commands.default.help.CmdHelp` formatters.

    Player-facing topics come from file/DB help entries (staff-authored).
    Staff may also see an ACL-filtered command index and ``help commands``.
    """
    from evennia.commands.cmdset import CmdSet
    from evennia.commands.default.help import CmdHelp, HelpCategory
    from evennia.help.catalog import (
        actor_for_help,
        actor_is_staff_for_help,
        collect_action_help_topics,
        is_action_help_topic,
        is_help_commands_topic,
        lookup_action_help_topic,
    )

    subtopics = list(subtopics or [])
    helper = CmdHelp()
    helper.caller = caller
    helper.session = session
    helper.topic = (topic or "").strip()
    helper.subtopics = subtopics
    helper.cmdset = cmdset if cmdset is not None else CmdSet()
    helper.clickable_topics = getattr(helper, "clickable_topics", True)

    def msg_help(text):
        if text:
            caller.msg(text)
        else:
            caller.msg(
                "No help topics are available. " "Try |whelp <topic>|n or |whelpsearch <query>|n."
            )

    helper.msg_help = msg_help
    actor = actor_for_help(caller, session)
    query = helper.topic

    if is_help_commands_topic(query):
        if not actor_is_staff_for_help(actor):
            msg_help(
                helper.format_help_entry(
                    topic=None,
                    help_text=f"There is no help topic matching '{query}'.",
                    suggested=[],
                    click_topics=helper.clickable_topics,
                )
            )
            return
        cmd_help_topics = collect_action_help_topics(actor, mode="list", staff_reference=True)
        cmd_help_by_category = defaultdict(list)
        for key, cmd in cmd_help_topics.items():
            cmd_help_by_category[cmd.help_category].append(key)
        msg_help(
            helper.format_help_index(
                cmd_help_by_category,
                {},
                click_topics=helper.clickable_topics,
            )
        )
        return

    if not query:
        _, db_help_topics, file_help_topics = helper.collect_topics(caller, mode="list")
        # Bare ``help`` lists staff-authored file/DB topics only. Action verbs
        # live under ``help commands`` (see ``HELP_INDEX_ACTIONS*`` settings).
        file_db_help_topics = {**file_help_topics, **db_help_topics}
        msg_help(_format_bare_index(helper, caller, actor, {}, file_db_help_topics))
        return

    _, db_help_topics, file_help_topics = helper.collect_topics(caller, mode="query")
    file_db_help_topics = {**file_help_topics, **db_help_topics}

    cmd_help_topics = {}
    if actor_is_staff_for_help(actor):
        cmd_help_topics = collect_action_help_topics(actor, mode="query", staff_reference=True)
        denied_topic, was_denied = lookup_action_help_topic(
            query, actor, include_denied=True, staff_reference=True
        )
        if (
            was_denied
            and denied_topic is not None
            and not _topic_in_file_db(file_db_help_topics, query)
        ):
            from evennia.help.catalog import action_help_requirement_desc

            req = action_help_requirement_desc(denied_topic.action_cls, actor)
            msg_help(
                helper.format_help_entry(
                    topic=denied_topic.key,
                    help_text=(f"|rYou do not have access to this command.|n\n|x{req or ''}|n"),
                    aliases=list(denied_topic.aliases),
                    suggested=[],
                    click_topics=helper.clickable_topics,
                )
            )
            return

    # Staff-authored file/DB prose wins over action-registry stubs on key clash.
    all_topics = {**cmd_help_topics, **file_db_help_topics}
    all_categories = list({HelpCategory(t.help_category) for t in all_topics.values()})
    entries = list(all_topics.values()) + all_categories
    match, suggestions = helper.do_search(query, entries)

    if not match:
        help_text = f"There is no help topic matching '{query}'."
        output = helper.format_help_entry(
            topic=None,
            help_text=help_text,
            suggested=suggestions,
            click_topics=helper.clickable_topics,
        )
        msg_help(output)
        return

    if isinstance(match, HelpCategory):
        category = match.key
        category_lower = category.lower()
        cmds_in_category = [
            key
            for key, cmd in cmd_help_topics.items()
            if category_lower == cmd.help_category.lower()
        ]
        topics_in_category = [
            key
            for key, ent in file_db_help_topics.items()
            if category_lower == ent.help_category.lower()
        ]
        output = helper.format_help_index(
            {category: cmds_in_category},
            {category: topics_in_category},
            title_lone_category=True,
            click_topics=helper.clickable_topics,
        )
        msg_help(output)
        return

    topic = match.key
    if inherits_from(match, "evennia.commands.command.Command") or is_action_help_topic(match):
        help_text = match.get_help(caller, helper.cmdset)
        aliases = match.aliases
        suggested = suggestions[1:] if suggestions else []
    else:
        help_text = match.entrytext
        aliases = match.aliases if isinstance(match.aliases, list) else match.aliases.all()
        suggested = suggestions[1:] if suggestions else []

    # Drill into any requested subtopic path (``help <topic>/<subtopic>/...``).
    # Subtopic text lives in the entry's ``# SUBTOPICS`` markup; the parser maps
    # it to a nested dict keyed by subtopic title, with the body under ``None``.
    subtopic_map = parse_entry_for_subcategories(help_text)
    help_text = subtopic_map[None]
    subtopic_index = [sub for sub in subtopic_map if sub is not None]
    sep = helper.subtopic_separator_char

    for subtopic_query in subtopics:
        if subtopic_query not in subtopic_map:
            # exact match failed - try a startswith- then an 'in'-match
            match_key = next(
                (key for key in subtopic_map if key and key.startswith(subtopic_query)),
                None,
            )
            if match_key is None:
                match_key = next(
                    (key for key in subtopic_map if key and subtopic_query in key), None
                )
            if match_key is None:
                checked = f"{topic}{sep}{subtopic_query}"
                msg_help(
                    helper.format_help_entry(
                        topic=topic,
                        help_text=f"No help entry found for '{checked}'",
                        subtopics=subtopic_index,
                        click_topics=helper.clickable_topics,
                    )
                )
                return
            subtopic_query = match_key
        subtopic_map = subtopic_map.pop(subtopic_query)
        subtopic_index = [sub for sub in subtopic_map if sub is not None]
        topic = f"{topic}{sep}{subtopic_query}"
    if subtopics:
        # below the top level we render the subtopic body, not the entry aliases
        help_text = subtopic_map[None]
        aliases = None

    output = helper.format_help_entry(
        topic=topic,
        help_text=help_text,
        aliases=aliases,
        subtopics=subtopic_index,
        suggested=suggested,
        click_topics=helper.clickable_topics,
    )
    msg_help(output)
