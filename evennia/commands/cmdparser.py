"""
The default command parser. Use your own by assigning
`settings.COMMAND_PARSER` to a Python path to a module containing the
replacing cmdparser function. The replacement parser must accept the
same inputs as the default one.

"""

import re

from django.conf import settings

from evennia.utils.logger import log_trace, mask_sensitive_input
from evennia.utils.multimatch import (_multimatch_regex,
                                      parse_multimatch_input,
                                      resolve_multimatch_index)


def create_match(cmdname, string, cmdobj, raw_cmdname):
    """
    Builds a command match by splitting the incoming string and
    evaluating the quality of the match.

    Args:
        cmdname (str): Name of command to check for.
        string (str): The string to match against.
        cmdobj (str): The full Command instance.
        raw_cmdname (str, optional): If CMD_IGNORE_PREFIX is set and the cmdname starts with
            one of the prefixes to ignore, this contains the raw, unstripped cmdname,
            otherwise it is None.

    Returns:
        match (tuple): This is on the form (cmdname, args, cmdobj, cmdlen, mratio, raw_cmdname),
            where `cmdname` is the command's name and `args` is the rest of the incoming
            string, without said command name. `cmdobj` is
            the Command instance, the cmdlen is the same as len(cmdname) and mratio
            is a measure of how big a part of the full input string the cmdname
            takes up - an exact match would be 1.0. Finally, the `raw_cmdname` is
            the cmdname unmodified by eventual prefix-stripping.

    """
    cmdlen, strlen = len(str(cmdname)), len(str(string))
    mratio = 1 - (strlen - cmdlen) / (1.0 * strlen)
    args = string[cmdlen:]
    return (cmdname, args, cmdobj, cmdlen, mratio, raw_cmdname)


def build_matches(raw_string, cmdset):
    """
    Build match tuples by matching raw_string against available commands.

    Args:
        raw_string (str): Input string that can look in any way; the only assumption is
            that the sought command's name/alias must be *first* in the string.
        cmdset (CmdSet): The current cmdset to pick Commands from.

    Returns:
        matches (list) A list of match tuples created by `cmdparser.create_match`.

    """
    matches = []
    try:
        search_string = raw_string.lower()
        for cmd in cmdset:
            cmdname, raw_cmdname = cmd.match(search_string)
            if cmdname:
                matches.append(create_match(cmdname, raw_string, cmd, raw_cmdname))
    except Exception:
        log_trace("cmdhandler error. raw_input:%s" % mask_sensitive_input(raw_string))
    return matches


def try_multimatch_differentiators(raw_string):
    """
    Parse multimatch disambiguation from user input (ordinals, last, other, N-name).

    Returns:
        selector, new_raw_string: selector is 0-based int, "last", "other", or None.
    """
    selector, new_raw_string = parse_multimatch_input(raw_string)
    if selector is not None:
        return selector, new_raw_string
    num_ref_match = _multimatch_regex().match(raw_string)
    if num_ref_match:
        mindex = int(num_ref_match.group("number")) - 1
        new_raw_string = num_ref_match.group("name") + (num_ref_match.group("args") or "")
        return mindex, new_raw_string
    return None, None


def try_num_differentiators(raw_string):
    """
    Backward-compatible alias for try_multimatch_differentiators.

    Returns:
        mindex, new_raw_string: For numeric selectors, mindex is 1-based for legacy callers.
        For "last"/"other", returns the string selector unchanged.
    """
    selector, new_raw_string = try_multimatch_differentiators(raw_string)
    if selector is None:
        return None, None
    if isinstance(selector, int):
        return selector + 1, new_raw_string
    return selector, new_raw_string


def cmdparser(raw_string, cmdset, caller, match_index=None, session=None, **kwargs):
    """
    This function is called by the cmdhandler once it has
    gathered and merged all valid cmdsets valid for this particular parsing.

    Args:
        raw_string (str): The unparsed text entered by the caller.
        cmdset (CmdSet): The merged, currently valid cmdset
        caller (Session, Account or Object): The caller triggering this parsing.
        match_index (int, optional): Index to pick a given match in a
            list of same-named command matches. If this is given, it suggests
            this is not the first time this function was called: normally
            the first run resulted in a multimatch, and the index is given
            to select between the results for the second run.

    Returns:
        matches (list): This is a list of match-tuples as returned by `create_match`.
            If no matches were found, this is an empty list.

    Notes:
        The cmdparser understand the following command combinations (where
        [] marks optional parts.

        ```
        [cmdname[ cmdname2 cmdname3 ...] [the rest]
        ```

        A command may consist of any number of space-separated words of any
        length, and contain any character. It may also be empty.

        The parser makes use of the cmdset to find command candidates. The
        parser return a list of matches. Each match is a tuple with its
        first three elements being the parsed cmdname (lower case),
        the remaining arguments, and the matched cmdobject from the cmdset.

    """
    if not raw_string:
        return []

    # single-pass token-boundary matching since +underspire.8. The old
    # second-pass that stripped CMD_IGNORE_PREFIXES is gone; prefix
    # characters are now load-bearing parts of the key.
    matches = build_matches(raw_string, cmdset)

    match_selector = None
    if not matches or len(matches) > 1:
        match_selector, new_raw_string = try_multimatch_differentiators(raw_string)
        if match_selector is not None:
            matches.extend(build_matches(new_raw_string, cmdset))

    # only select command matches we are actually allowed to call.
    if getattr(settings, "CMD_ACCESS_CACHE_ENABLED", False):
        from evennia.commands.cmd_access_cache import cached_cmd_access

        matches = [
            match for match in matches if cached_cmd_access(match[2], caller, session=session)
        ]
    else:
        matches = [match for match in matches if match[2].access(caller, "cmd", session=session)]

    # try to bring the number of matches down to 1
    if len(matches) > 1:
        # See if it helps to analyze the match with preserved case but only if
        # it leaves at least one match.
        trimmed = [match for match in matches if raw_string.startswith(match[0])]
        if trimmed:
            matches = trimmed

    if len(matches) > 1:
        # we still have multiple matches. Sort them by count quality.
        matches = sorted(matches, key=lambda m: m[3])
        # only pick the matches with highest count quality
        quality = [mat[3] for mat in matches]
        matches = matches[-quality.count(quality[-1]) :]

    if len(matches) > 1:
        # still multiple matches. Fall back to ratio-based quality.
        matches = sorted(matches, key=lambda m: m[4])
        # only pick the highest rated ratio match
        quality = [mat[4] for mat in matches]
        matches = matches[-quality.count(quality[-1]) :]

    if len(matches) > 1 and match_selector is not None:
        idx = resolve_multimatch_index(match_selector, len(matches))
        if idx is not None:
            matches = [matches[idx]]
        else:
            matches = []

    # no matter what we have at this point, we have to return it.
    return matches
