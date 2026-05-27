"""
Search and multimatch handling (game overrides)

Evennia core (evennia.utils.multimatch and at_search_result) provides by default:

- Ordinal display and input: first/second/last/other (two matches), 1-name
- Location scopes: my, here, worn (see SEARCH_MULTIMATCH_LOCATION_PREFIXES)
- Auto-pick when all matches are in one inventory or room bucket

Override only if you need custom behavior:

    SEARCH_AT_RESULT = "server.conf.at_search.at_search_result"
    SEARCH_MULTIMATCH_INPUT = "server.conf.at_search.parse_multimatch_input"

"""


def at_search_result(matches, caller, query="", quiet=False, **kwargs):
    """
    Optional override for multimatch / not-found messaging.
    Leave empty to use the core default (settings.SEARCH_AT_RESULT).
    """
    pass
