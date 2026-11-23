"""Search mixin for DefaultObject."""

import re

from django.conf import settings
from django.utils.translation import gettext as _

import evennia
from evennia.hooks import hook
from evennia.objects.search_result import Ambiguous, Found, NotFound, SearchResult
from evennia.utils import search as _search_utils
from evennia.utils.multimatch import (
    narrow_candidates,
    parse_search_qualifiers,
    resolve_multimatch_index,
    try_autopick,
)
from evennia.utils.ansi import strip_ansi
from evennia.utils.utils import dbref, make_iter, variable_from_module

_AT_SEARCH_RESULT = variable_from_module(*settings.SEARCH_AT_RESULT.rsplit(".", 1))


class SearchMixin:
    """Mixin providing search-related methods for DefaultObject."""

    @hook(
        event="search",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=("DefaultObject.search",),
        notes="Search-pipeline stage 1: rewrite the raw search string (nick replacement etc).",
    )
    def get_search_query_replacement(self, searchdata, **kwargs):
        """
        This method is called by the search method to allow for direct
        replacements of the search string before it is used in the search.

        Args:
            searchdata (str): The search string to replace.
            **kwargs (any): These are the same as passed to the `search` method.

        Returns:
            str: The (potentially modified) search string.

        """
        if kwargs.get("use_nicks"):
            return self.nicks.nickreplace(
                searchdata, categories=("object", "account"), include_account=True
            )
        return searchdata

    @hook(
        event="search",
        phase="composite",
        actor="self",
        returns="conditional",
        discipline="public",
        fires_from=("DefaultObject.search",),
        notes="Search-pipeline stage 2: short-circuit returns (me/self/here). Returns (should_return, result).",
    )
    def get_search_direct_match(self, searchdata, **kwargs):
        """
        This method is called by the search method to allow for direct
        replacements, such as 'me' always being an alias for this object.

        Args:
            searchdata (str): The search string to replace.
            **kwargs (any): These are the same as passed to the `search` method.

        Returns:
            tuple: A tuple `(should_return, str or Obj)`, where `should_return` is a boolean
            indicating the `.search` method should return the result immediately without further
            processing. If `should_return` is `True`, the second element of the tuple is the result
            that is returned.

        """
        if isinstance(searchdata, str):
            candidates = kwargs.get("candidates") or []
            global_search = kwargs.get("global_search", False)
            match searchdata.lower():
                case "me" | "self":
                    return global_search or self in candidates, self
                case "here":
                    return global_search or self.location in candidates, self.location
        return False, searchdata

    @hook(
        event="search",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=("DefaultObject.search",),
        notes="Search-pipeline stage 3: compute candidate set (location/contents-aware).",
    )
    def get_search_candidates(self, searchdata, **kwargs):
        """
        Helper for the `.search` method. Get the candidates for a search. Also the `candidates`
        provided to the search function is included, and could be modified in-place here.

        Args:
            searchdata (str): The search criterion (could be modified by
                `get_search_query_replacement`).
            **kwargs (any): These are the same as passed to the `search` method.

        Returns:
            list: A list of objects possibly relevant for the search.

        Notes:
            If `searchdata` is a #dbref, this method should always return `None`. This is because
            the search should always be global in this case. If `candidates` were already given,
            they should be used as is. If `location` was given, the candidates should be based on
            that.

        """
        if kwargs.get("global_search") or dbref(searchdata):
            # global searches (dbref-searches are always global too) should not have any candidates
            return None

        # if candidates were already given, use them
        candidates = kwargs.get("candidates")
        if candidates is not None:
            return candidates

        scope = kwargs.get("_search_scope")
        if scope:
            scoped = narrow_candidates(self, scope)
            if scoped is not None:
                return scoped

        # find candidates based on location
        location = kwargs.get("location")

        if location:
            # location(s) were given
            candidates = []
            for obj in make_iter(location):
                candidates.extend(obj.contents)
        else:
            # local search. Candidates are taken from
            # self.contents, self.location and
            # self.location.contents
            location = self.location
            candidates = self.contents
            if location:
                candidates = candidates + [location] + location.contents
            else:
                # normally we don't need this since we are
                # included in location.contents
                candidates.append(self)
        return candidates

    @hook(
        event="search",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=("DefaultObject.search",),
        notes="Search-pipeline stage 4: actual ObjectDB query. Returns queryset/iterable.",
    )
    def get_search_result(
        self,
        searchdata,
        attribute_name=None,
        typeclass=None,
        candidates=None,
        exact=False,
        use_dbref=None,
        tags=None,
        **kwargs,
    ):
        """
        Helper for the `.search` method. This is a wrapper for actually searching for objects, used
        by the `search` method. This is broken out into a separate method to allow for easier
        overriding in child classes.

        Args:
            searchdata (str): The search criterion.
            attribute_name (str): The attribute to search on (default is `.
            typeclass (Typeclass or list): The typeclass to search for.
            candidates (list): A list of objects to search between.
            exact (bool): Require exact match.
            use_dbref (bool): Allow dbref search.
            tags (list): Tags to search for.

        Returns:
            queryset or iterable: The result of the search.

        """
        from evennia.objects.models import ObjectDB

        return ObjectDB.objects.search_object(
            searchdata,
            attribute_name=attribute_name,
            typeclass=typeclass,
            candidates=candidates,
            exact=exact,
            use_dbref=use_dbref,
            tags=tags,
        )

    @hook(
        event="search",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=("DefaultObject.search",),
        notes="Names self can find obj by, read by prefer to tell what a match names.",
    )
    def get_search_names(self, obj):
        """
        The names `self` can find `obj` by.

        `search_for` reads them when `prefer` is set, to tell whether the typed words end
        one of the names of each match. Games that find objects by other names (a short
        description, a name that depends on the searcher) override this to return them.

        Args:
            obj (Object): An object the search matched.

        Returns:
            list: Name strings.

        """
        return [obj.key, *obj.aliases.all()]

    @hook(
        event="search",
        phase="composite",
        actor="self",
        returns="conditional",
        discipline="public",
        fires_from=("DefaultObject.search",),
        notes="Search-pipeline stage 5: collapse duplicate matches into stacks. Returns (stacked, results).",
    )
    def get_stacked_results(self, results, **kwargs):
        """
        This method is called by the search method to allow for handling of multi-match
        results that should be stacked.

        Args:
            results (list): The list of results from the search.

        Returns:
            tuple: A tuple `(stacked, results)`, where `stacked` is a boolean indicating if the
            result is stacked and `results` is the list of results to return. If `stacked`
            is True, the ".search" method will return `results` immediately without further
            processing (it will not result in a multimatch-error).

        Notes:
            The `stacked` keyword argument is an integer that controls the max size of each stack
            (if >0). It's important to make sure to only stack _identical_ objects, otherwise we
            risk losing track of objects.

        """
        from evennia.objects.models import ObjectDB

        nresults = len(results)
        max_stack_size = kwargs.get("stacked", 0)
        typeclass = kwargs.get("typeclass")
        exact = kwargs.get("exact", False)

        if max_stack_size > 0 and nresults > 1:
            nstack = nresults
            if not exact:
                # we re-run exact match against one of the matches to make sure all are indeed
                # equal and we were not catching partial matches not belonging to the stack
                nstack = len(
                    ObjectDB.objects.get_objs_with_key_or_alias(
                        results[0].key,
                        exact=True,
                        candidates=list(results),
                        typeclasses=[typeclass] if typeclass else None,
                    )
                )
            if nstack == nresults:
                # a valid stack of identical items, return multiple results
                return True, list(results)[:max_stack_size]

        return False, results

    def search_for(
        self,
        searchdata,
        global_search=False,
        use_nicks=True,
        typeclass=None,
        location=None,
        attribute_name=None,
        exact=False,
        candidates=None,
        use_locks=True,
        use_dbref=None,
        tags=None,
        stacked=0,
        prefer=None,
    ):
        """
        Run the search pipeline and return a typed `SearchResult`.

        This is the primitive: it never emits messages. The result is one of
        `Found`, `Ambiguous`, or `NotFound` (see `evennia.objects.search_result`).

        For the common "find one or fail with a default prompt" case, use
        `.search()` instead, which wraps this and emits the default
        disambiguation/not-found prompt via `settings.SEARCH_AT_RESULT`.

        Args:
            searchdata (str or obj): See `.search` for full details on this and
                all filter arguments.
            global_search, use_nicks, typeclass, location, attribute_name,
            exact, candidates, use_locks, use_dbref, tags, stacked, prefer: See
                `.search` for full descriptions.

        Returns:
            SearchResult: One of `Found(obj[, stack])`, `Ambiguous(candidates,
            search_string[, invalid_other])`, or `NotFound(search_string)`.

        """
        original_searchdata = searchdata

        # store input kwargs for sub-methods (this must be done first)
        input_kwargs = {
            key: value
            for key, value in locals().items()
            if key not in ("self", "searchdata", "original_searchdata")
        }

        # replace incoming searchdata string with a potentially modified version
        searchdata = self.get_search_query_replacement(searchdata, **input_kwargs)

        quals = parse_search_qualifiers(searchdata)
        input_kwargs["_search_scope"] = quals["scope"]
        input_kwargs["_search_selector"] = quals["selector"]
        input_kwargs["_search_had_qualifier"] = quals["had_qualifier"]
        searchdata = quals["searchdata"]

        # get candidates
        candidates = self.get_search_candidates(searchdata, **input_kwargs)

        # handle special input strings, like "me" or "here".
        should_return, direct = self.get_search_direct_match(
            searchdata, **(input_kwargs | {"candidates": candidates})
        )
        if should_return:
            return Found(obj=direct)

        # if use_dbref is None, we use a lock to determine if dbref search is allowed
        if use_dbref is None:
            from evennia.authorization.service import has_capability

            use_dbref = has_capability(self, "engine.world.search", resource=self)

        # convert tags into tag tuples suitable for query
        tags = [
            (tagkey, tagcat[0] if tagcat else None) for tagkey, *tagcat in make_iter(tags or [])
        ]

        # always use exact match for dbref/global searches
        exact = True if global_search or dbref(searchdata) else exact

        return self._search_pass(
            searchdata,
            original_searchdata,
            candidates,
            input_kwargs,
            attribute_name=attribute_name,
            typeclass=typeclass,
            exact=exact,
            use_dbref=use_dbref,
            tags=tags,
            use_locks=use_locks,
            prefer=prefer if candidates is not None else None,
        )

    def _search_pass(
        self,
        searchdata,
        original_searchdata,
        candidates,
        input_kwargs,
        *,
        attribute_name,
        typeclass,
        exact,
        use_dbref,
        tags,
        use_locks,
        prefer,
    ):
        """
        Match `searchdata` against the candidates and collapse the matches to a result.

        Args:
            searchdata (str): The search string, with qualifiers already parsed out.
            original_searchdata (str): The string as the caller gave it, for the result.
            candidates (list or None): The objects to match against; None for a global search.
            input_kwargs (dict): The search's keyword arguments, for the stacking hook.
            attribute_name, typeclass, exact, use_dbref, tags, use_locks: As for `search_for`,
                after `search_for` has resolved them.
            prefer (callable or None): Narrows a match that would otherwise be ambiguous.

        Returns:
            SearchResult: `Found`, `Ambiguous` or `NotFound`.

        """
        # do the actual search
        results = self.get_search_result(
            searchdata,
            attribute_name=attribute_name,
            typeclass=typeclass,
            candidates=candidates,
            exact=exact,
            use_dbref=use_dbref,
            tags=tags,
        )

        # filter out objects we are not allowed to search
        if use_locks:
            results = [x for x in list(results) if x.access(self, "search", default=True)]
        else:
            results = list(results)

        # apply ordinal selector ("first", "2-sword", etc.)
        invalid_other = False
        selector = input_kwargs.get("_search_selector")
        if selector is not None:
            nresults = len(results)
            if selector == "other" and nresults != 2:
                invalid_other = True
            else:
                idx = resolve_multimatch_index(selector, nresults)
                if idx is not None:
                    results = [results[idx]]
                else:
                    results = []
        elif (
            prefer is not None and len(results) > 1 and self._each_named_by_end(searchdata, results)
        ):
            from evennia.actions.perception import filter_visible

            preferred = [obj for obj in results if prefer(obj)]
            if filter_visible(self, preferred):
                results = preferred

        # handle stacked objects (multiple identical matches collapsed to a stack)
        is_stacked, results = self.get_stacked_results(results, **input_kwargs)
        if is_stacked:
            return Found(obj=results[0], stack=results)

        # collapse to typed result
        if not results:
            return NotFound(search_string=original_searchdata)

        had_qualifier = input_kwargs.get("_search_had_qualifier")
        if len(results) > 1 and not had_qualifier:
            picked = try_autopick(results, self)
            if picked is not None:
                return Found(obj=picked)

        if len(results) == 1:
            return Found(obj=results[0])

        ambiguous = Ambiguous(
            candidates=results,
            search_string=original_searchdata,
            invalid_other=invalid_other,
        )
        return ambiguous

    def _each_named_by_end(self, searchdata, matches):
        """
        True when the typed words end a name of every match.

        "jacket" ends "bomber jacket" and "plaid jacket", so the player named two jackets
        and `prefer` may pick between them. "gas mask" does not end "gas mask filter", so
        the player may mean the filter or the mask, and the search asks which.
        """
        words = strip_ansi(str(searchdata)).lower().split()
        if words and words[0] in ("a", "an", "the") and len(words) > 1:
            words = words[1:]
        if not words:
            return False
        pattern = re.compile(r".* ".join(r"\b" + re.escape(word) for word in words) + r"\S*$")
        return all(
            any(
                pattern.search(" ".join(strip_ansi(str(name)).lower().split()))
                for name in self.get_search_names(obj)
            )
            for obj in matches
        )

    def search(
        self,
        searchdata,
        global_search=False,
        use_nicks=True,
        typeclass=None,
        location=None,
        attribute_name=None,
        exact=False,
        candidates=None,
        use_locks=True,
        not_found=None,
        ambiguous=None,
        use_dbref=None,
        tags=None,
        stacked=0,
        prefer=None,
    ):
        """
        Search for an object and return it (or None on failure).

        Convenience wrapper around `.search_for()`. On `Found`, returns the
        matched object (or the stack list if `stacked` was set and matched).
        On `Ambiguous` or `NotFound`, emits the default error/disambiguation
        prompt via `settings.SEARCH_AT_RESULT` and returns `None`.

        Callers that want to handle ambiguity or no-match themselves (without
        the default prompt) should call `.search_for()` and pattern-match on
        the typed result.

        Args:
            searchdata (str or obj): Primary search criterion. Will be matched
                against `object.key` (with `object.aliases` second) unless
                the keyword attribute_name specifies otherwise.

                Special keywords:

                - `#<num>`: search by unique dbref. This is always a global search.
                - `me,self`: self-reference to this object
                - Ordinal disambiguation: `first <string>`, `second <string>`, `last <string>`,
                  `other <string>` (two matches only), or `<num>-<string>` per
                  `settings.SEARCH_MULTIMATCH_REGEX`.
                - Location scope: `my <string>`, `here <string>`, `worn <string>` (see
                  `settings.SEARCH_MULTIMATCH_LOCATION_PREFIXES`).

            global_search (bool): Search all objects globally. This overrules 'location' data.
            use_nicks (bool): Use nickname-replace (nicktype "object") on `searchdata`.
            typeclass (str or Typeclass, or list of either): Limit search only
                to `Objects` with this typeclass. May be a list of typeclasses
                for a broader search.
            location (Object or list): Specify a location or multiple locations
                to search. Note that this is used to query the *contents* of a
                location and will not match for the location itself -
                if you want that, don't set this or use `candidates` to specify
                exactly which objects should be searched. If this nor candidates are
                given, candidates will include caller's inventory, current location and
                all objects in the current location.
            attribute_name (str): Define which property to search. If set, no
                key+alias search will be performed.
            exact (bool): If unset (default) - prefers to match to beginning of
                string rather than not matching at all. If set, requires
                exact matching of entire string.
            candidates (list of objects): Optional custom list of objects
                to search (filter) between. Ignored if `global_search` is given.
            use_locks (bool): If True (default), removes search results which
                fail the "search" lock.
            not_found (str): Optional custom string for the not-found prompt.
            ambiguous (str): Optional custom string for the multimatch prompt header.
            use_dbref (bool or None, optional): If `True`, allow dbref queries like "#123".
                If `None` (default), turned on for Builders, off otherwise.
            tags (list or tuple): Find objects matching one or more Tags. Each is
                `tagname` or `(tagname, tagcategory)`.
            stacked (int, optional): If > 0, multimatches of identical objects collapse
                into a stack of up to `stacked` items. Returned as a list in that case.
            prefer (callable, optional): `prefer(obj) -> bool`, true for the objects the
                caller can act on. It only settles a match that would otherwise ask
                which one, and only when the typed words end a name of every match (see
                `get_search_names`): then the visible matches that pass it are kept. So
                "jacket" takes a worn bomber jacket over a carried plaid jacket for a verb
                that acts on worn things, but "gas mask" still asks between a gas mask and
                a gas mask filter, and a name that matches one object better than the rest
                still finds that object, for the caller's own refusal. An ordinal
                ("2 jacket") counts the full list. Ignored for global searches.

        Returns:
            Object, list, or None: The matched object on a unique match; a list
            of stacked identical objects when `stacked > 0` matched; or `None`
            on no/multi-match (with the prompt emitted via `SEARCH_AT_RESULT`).

        Notes:
            To find Accounts, use eg. `evennia.account_search`.

        """
        result = self.search_for(
            searchdata,
            global_search=global_search,
            use_nicks=use_nicks,
            typeclass=typeclass,
            location=location,
            attribute_name=attribute_name,
            exact=exact,
            candidates=candidates,
            use_locks=use_locks,
            use_dbref=use_dbref,
            tags=tags,
            stacked=stacked,
            prefer=prefer,
        )

        if isinstance(result, Found):
            return result.stack if result.stack is not None else result.obj

        matches = result.candidates if isinstance(result, Ambiguous) else []
        return _AT_SEARCH_RESULT(
            matches,
            self,
            query=result.search_string,
            nofound_string=not_found,
            multimatch_string=ambiguous,
            invalid_other=isinstance(result, Ambiguous) and result.invalid_other,
            _search_had_qualifier=True,
        )

    def search_account(self, searchdata, quiet=False):
        """
        Simple shortcut wrapper to search for accounts, not characters.

        Args:
            searchdata (str): Search criterion - the key or dbref of the account
                to search for. If this is "here" or "me", search
                for the account connected to this object.
            quiet (bool): Returns the results as a list rather than
                echo eventual standard error messages. Default `False`.

        Returns:
            DefaultAccount, None or list: What is returned depends on
            the `quiet` setting:

            - `quiet=False`: No match or multumatch auto-echoes errors
              to self.msg, then returns `None`. The esults are passed
              through `settings.SEARCH_AT_RESULT` and
              `settings.SEARCH_AT_MULTIMATCH_INPUT`. If there is a
              unique match, this will be returned.
            - `quiet=True`: No automatic error messaging is done, and
              what is returned is always a list with 0, 1 or more
              matching Accounts.

        """
        if isinstance(searchdata, str):
            # searchdata is a string; wrap some common self-references
            if searchdata.lower() in ("me", "self"):
                return [self.account] if quiet else self.account

        results = _search_utils.search_account(searchdata)

        if quiet:
            return results
        return _AT_SEARCH_RESULT(results, self, query=searchdata)
