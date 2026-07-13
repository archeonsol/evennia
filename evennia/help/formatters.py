"""
Generic help-formatting surface, reusable by both the help renderer and the
action engine without dragging in command-dispatch machinery.

This houses the formatter, search, and permission helpers that were previously
methods on ``CmdHelp``, lifted onto a plain :class:`HelpFormatter` object so the
help render path and the ``@sethelp`` flow can share the exact same logic
without constructing a ``Command`` (or a throwaway ``CmdSet``).

"""

from dataclasses import dataclass

from django.conf import settings

from evennia.help.filehelp import FILE_HELP_ENTRIES
from evennia.help.models import HelpEntry
from evennia.help.utils import help_search_with_index
from evennia.utils.ansi import ANSIString
from evennia.utils.utils import dedent, format_grid, inherits_from, pad

HELP_CLICKABLE_TOPICS = settings.HELP_CLICKABLE_TOPICS

__all__ = ("HelpFormatter", "HelpCategory", "_loadhelp", "_savehelp", "_quithelp")


@dataclass
class HelpCategory:
    """
    Mock 'help entry' to search categories with the same code.

    """

    key: str

    @property
    def search_index_entry(self):
        return {
            "key": self.key,
            "aliases": "",
            "category": self.key,
            "tags": "",
            "text": "",
        }

    def __hash__(self):
        return hash(id(self))


class HelpFormatter:
    """
    Stateful help formatter/search/permission helper.

    Carries the configuration and session state needed to render help entries
    and indexes, run help-queries, and apply read/list permission checks. It
    subclasses nothing, so it carries no ``Command`` shape (no ``func``,
    ``parse``, ``return_cmdset``, etc.); consumers construct it, set ``caller``,
    ``session``, and ``account`` (and optionally ``topic``/``subtopics``/
    ``clickable_topics``) as attributes, then call the formatter methods.

    """

    # colors for the help index
    index_type_separator_clr = "|w"
    index_category_clr = "|W"
    index_topic_clr = "|G"

    # suggestion cutoff, between 0 and 1 (1 => perfect match)
    suggestion_cutoff = 0.6

    # number of suggestions (set to 0 to remove suggestions from help)
    suggestion_maxnum = 5

    # separator between subtopics:
    subtopic_separator_char = r"/"

    # should topics disply their help entry when clicked
    clickable_topics = HELP_CLICKABLE_TOPICS

    def __init__(self, caller=None, session=None, account=None):
        self.caller = caller
        self.session = session
        self.account = account

    def client_width(self):
        """
        Get the client screenwidth for the session using this command.

        Returns:
            client width (int): The width (in characters) of the client window.

        """
        if self.session:
            return self.session.protocol_flags.get(
                "SCREENWIDTH", {0: settings.CLIENT_DEFAULT_WIDTH}
            )[0]
        return settings.CLIENT_DEFAULT_WIDTH

    def format_help_entry(
        self,
        topic="",
        help_text="",
        aliases=None,
        suggested=None,
        subtopics=None,
        click_topics=True,
    ):
        """This visually formats the help entry.
        This method can be overridden to customize the way a help
        entry is displayed.

        Args:
            title (str, optional): The title of the help entry.
            help_text (str, optional): Text of the help entry.
            aliases (list, optional): List of help-aliases (displayed in header).
            suggested (list, optional): Strings suggested reading (based on title).
            subtopics (list, optional): A list of strings - the subcategories available
                for this entry.
            click_topics (bool, optional): Should help topics be clickable. Default is True.

        Returns:
            help_message (str): Help entry formated for console.

        """
        separator = "|C" + "-" * self.client_width() + "|n"
        start = f"{separator}\n"

        title = f"|CHelp for |w{topic}|n" if topic else "|rNo help found|n"

        if aliases:
            aliases = " |C(aliases: {}|C)|n".format(
                "|C,|n ".join(f"|w{ali}|n" for ali in aliases)
            )
        else:
            aliases = ""

        help_text = "\n" + dedent(help_text.strip("\n")) if help_text else ""

        if subtopics:
            if click_topics:
                subtopics = [
                    f"|lchelp {topic}/{subtop}|lt|w{topic}/{subtop}|n|le"
                    for subtop in subtopics
                ]
            else:
                subtopics = [f"|w{topic}/{subtop}|n" for subtop in subtopics]
            subtopics = "\n|CSubtopics:|n\n  {}".format(
                "\n  ".join(
                    format_grid(
                        subtopics,
                        width=self.client_width(),
                        line_prefix=self.index_topic_clr,
                    )
                )
            )
        else:
            subtopics = ""

        if suggested:
            suggested = sorted(suggested)
            if click_topics:
                suggested = [f"|lchelp {sug}|lt|w{sug}|n|le" for sug in suggested]
            else:
                suggested = [f"|w{sug}|n" for sug in suggested]
            suggested = "\n|COther topic suggestions:|n\n{}".format(
                "\n  ".join(
                    format_grid(
                        suggested,
                        width=self.client_width(),
                        line_prefix=self.index_topic_clr,
                    )
                )
            )
        else:
            suggested = ""

        end = start

        partorder = (start, title + aliases, help_text, subtopics, suggested, end)

        return "\n".join(part.rstrip() for part in partorder if part)

    def format_help_index(
        self,
        cmd_help_dict=None,
        db_help_dict=None,
        title_lone_category=False,
        click_topics=True,
    ):
        """Output a category-ordered g for displaying the main help, grouped by
        category.

        Args:
            cmd_help_dict (dict): A dict `{"category": [topic, topic, ...]}` for
                command-based help.
            db_help_dict (dict): A dict `{"category": [topic, topic], ...]}` for
                database-based help.
            title_lone_category (bool, optional): If a lone category should
                be titled with the category name or not. While pointless in a
                general index, the title should probably show when explicitly
                listing the category itself.
            click_topics (bool, optional): If help-topics are clickable or not
                (for webclient or telnet clients with MXP support).
        Returns:
            str: The help index organized into a grid.

        Notes:
            The input are the pre-loaded help files for commands and database-helpfiles
            respectively. You can override this method to return a custom display of the list of
            commands and topics.

        """

        def _group_by_category(help_dict):
            grid = []
            verbatim_elements = []

            if len(help_dict) == 1 and not title_lone_category:
                # don't list categories if there is only one
                for category in help_dict:
                    # gather and sort the entries from the help dictionary
                    entries = sorted(set(help_dict.get(category, [])))

                    # make the help topics clickable
                    if click_topics:
                        entries = [f"|lchelp {entry}|lt{entry}|le" for entry in entries]

                    # add the entries to the grid
                    grid.extend(entries)
            else:
                # list the categories
                for category in sorted(set(list(help_dict.keys()))):
                    category_str = f"-- {category.title()} "
                    grid.append(
                        ANSIString(
                            self.index_category_clr
                            + category_str
                            + "-" * (width - len(category_str))
                            + self.index_topic_clr
                        )
                    )
                    verbatim_elements.append(len(grid) - 1)

                    # gather and sort the entries from the help dictionary
                    entries = sorted(set(help_dict.get(category, [])))

                    # make the help topics clickable
                    if click_topics:
                        entries = [f"|lchelp {entry}|lt{entry}|le" for entry in entries]

                    # add the entries to the grid
                    grid.extend(entries)

            return grid, verbatim_elements

        help_index = ""
        width = self.client_width()
        grid = []
        verbatim_elements = []
        cmd_grid, db_grid = "", ""

        if any(cmd_help_dict.values()):
            # get the command-help entries by-category
            sep1 = (
                self.index_type_separator_clr
                + pad("Commands", width=width, fillchar="-")
                + self.index_topic_clr
            )
            grid, verbatim_elements = _group_by_category(cmd_help_dict)
            gridrows = format_grid(
                grid,
                width,
                sep="  ",
                verbatim_elements=verbatim_elements,
                line_prefix=self.index_topic_clr,
            )
            cmd_grid = ANSIString("\n").join(gridrows) if gridrows else ""

        if any(db_help_dict.values()):
            # get db-based help entries by-category
            sep2 = (
                self.index_type_separator_clr
                + pad("Game & World", width=width, fillchar="-")
                + self.index_topic_clr
            )
            grid, verbatim_elements = _group_by_category(db_help_dict)
            gridrows = format_grid(
                grid,
                width,
                sep="  ",
                verbatim_elements=verbatim_elements,
                line_prefix=self.index_topic_clr,
            )
            db_grid = ANSIString("\n").join(gridrows) if gridrows else ""

        # only show the main separators if there are actually both cmd and db-based help
        if cmd_grid and db_grid:
            help_index = f"{sep1}\n{cmd_grid}\n{sep2}\n{db_grid}"
        else:
            help_index = f"{cmd_grid}{db_grid}"

        return help_index

    def can_read_topic(self, cmd_or_topic, caller):
        """
        Helper method. If this return True, the given help topic
        be viewable in the help listing. Note that even if this returns False,
        the entry will still be visible in the help index unless `should_list_topic`
        is also returning False.

        Args:
            cmd_or_topic (Command, HelpEntry or FileHelpEntry): The topic/command to test.
            caller: the caller checking for access.

        Returns:
            bool: If command can be viewed or not.

        Notes:
            This uses the 'read' lock. If no 'read' lock is defined, the topic is assumed readable
            by all.

        """
        if inherits_from(cmd_or_topic, "evennia.commands.command.Command"):
            return cmd_or_topic.auto_help and cmd_or_topic.access(
                caller, "read", default=True
            )
        from evennia.help.catalog import is_action_help_topic

        if is_action_help_topic(cmd_or_topic):
            return cmd_or_topic.auto_help and cmd_or_topic.access(
                caller, "read", default=True, session=self.session
            )
        else:
            return cmd_or_topic.access(caller, "read", default=True)

    def can_list_topic(self, cmd_or_topic, caller):
        """
        Should the specified command appear in the help table?

        This method only checks whether a specified command should appear in the table of
        topics/commands.  The command can be used by the caller (see the 'should_show_help' method)
        and the command will still be available, for instance, if a character type 'help name of the
        command'.  However, if you return False, the specified command will not appear in the table.
        This is sometimes useful to "hide" commands in the table, but still access them through the
        help system.

        Args:
            cmd_or_topic (Command, HelpEntry or FileHelpEntry): The topic/command to test.
            caller: the caller checking for access.

        Returns:
            bool: If command should be listed or not.

        Notes:
            ``auto_help`` is checked for commands. All sources use their typed
            ``view`` policy and fall back to ``read`` when no view policy exists.

        """
        from evennia.help.catalog import is_action_help_topic

        if is_action_help_topic(cmd_or_topic):
            if not cmd_or_topic.auto_help:
                return False
            return cmd_or_topic.access(
                caller, "view", default=True, session=self.session
            )

        if hasattr(cmd_or_topic, "auto_help") and not cmd_or_topic.auto_help:
            return False

        has_view = (
            cmd_or_topic.policies.get("view")
            if hasattr(cmd_or_topic, "policies")
            else None
        )
        if has_view is not None:
            return cmd_or_topic.access(caller, "view", default=True)
        return cmd_or_topic.access(caller, "read", default=True)

    def collect_topics(self, caller, mode="list"):
        """
        Collect help topics from all sources (cmd/db/file).

        Args:
            caller (Object or Account): The user of the Command.
            mode (str): One of 'list' or 'query', where the first means we are collecting to view
                the help index and the second because of wanting to search for a specific help
                entry/cmd to read. This determines which access should be checked.

        Returns:
            tuple: A tuple of three dicts containing the different types of help entries
            in the order cmd-help, db-help, file-help:
                `({key: cmd,...}, {key: dbentry,...}, {key: fileentry,...}`

        """
        from evennia.help.catalog import (
            actor_for_help, collect_action_help_topics,
            should_include_action_topics_in_index)

        actor = actor_for_help(caller, self.session)
        cmd_help_topics = {}
        if should_include_action_topics_in_index(actor):
            cmd_help_topics = collect_action_help_topics(
                actor, mode=mode, staff_reference=True
            )
        file_help_topics = {
            topic.key.lower().strip(): topic for topic in FILE_HELP_ENTRIES.all()
        }
        db_help_topics = {
            topic.key.lower().strip(): topic for topic in HelpEntry.objects.all()
        }
        if mode == "list":
            db_help_topics = {
                key: entry
                for key, entry in db_help_topics.items()
                if self.can_list_topic(entry, caller)
            }
            file_help_topics = {
                key: entry
                for key, entry in file_help_topics.items()
                if self.can_list_topic(entry, caller)
            }
        else:
            db_help_topics = {
                key: entry
                for key, entry in db_help_topics.items()
                if self.can_read_topic(entry, caller)
            }
            file_help_topics = {
                key: entry
                for key, entry in file_help_topics.items()
                if self.can_read_topic(entry, caller)
            }

        return cmd_help_topics, db_help_topics, file_help_topics

    def do_search(self, query, entries, search_fields=None):
        """
        Perform a help-query search, default using Lunr search engine.

        Args:
            query (str): The help entry to search for.
            entries (list): All possibilities. A mix of commands, HelpEntries and FileHelpEntries.
            search_fields (list): A list of dicts defining how Lunr will find the
                search data on the elements. If not given, will use a default.

        Returns:
            tuple: A tuple (match, suggestions).

        """

        if not search_fields:
            # lunr search fields/boosts
            search_fields = [
                {"field_name": "key", "boost": 10},
                {"field_name": "aliases", "boost": 7},
                {"field_name": "category", "boost": 5},
                {"field_name": "tags", "boost": 1},  # tags are not used by default
            ]
        match, suggestions = None, None
        for match_query in (query, f"{query}*"):
            # We first do an exact word-match followed by a start-by query. The
            # return of this will either be a HelpCategory, a Command or a
            # HelpEntry/FileHelpEntry.
            matches, suggestions = help_search_with_index(
                match_query,
                entries,
                suggestion_maxnum=self.suggestion_maxnum,
                fields=search_fields,
            )
            # Move an exact key/alias match to the front of the list.
            for m in matches[:]:
                aliases = [m.key]
                if not isinstance(m, HelpCategory):
                    # Aliases for help created with 'sethelp' is an AliasHandler
                    aliases += (
                        list(m.aliases)
                        if isinstance(m.aliases, (list, tuple))
                        else m.aliases.all()
                    )
                if query in aliases:
                    matches.remove(m)
                    matches.insert(0, m)
                    break
            if matches:
                match = matches[0]
                break
        if match:
            # Move an exact suggestion match to the front of the list
            for s in suggestions[:]:
                if query == s:
                    suggestions.remove(s)
                    suggestions.insert(0, s)
                    break

        return match, suggestions


def _loadhelp(caller):
    entry = caller.db._editing_help
    if entry:
        return entry.entrytext
    else:
        return ""


def _savehelp(caller, buffer):
    entry = caller.db._editing_help
    caller.msg("Saved help entry.")
    if entry:
        entry.entrytext = buffer


def _quithelp(caller):
    caller.msg("Closing the editor.")
    del caller.db._editing_help
