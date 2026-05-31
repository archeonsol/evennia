"""Typed search-result variants returned by `search_for`.

The `.search_for()` primitive on `DefaultObject` / `DefaultAccount` returns
one of three frozen dataclasses: `Found`, `Ambiguous`, or `NotFound`. The
sugar wrapper `.search()` collapses these to `Object | None` and emits the
default disambiguation prompt; callers that want to handle the variants
themselves use `.search_for()` and pattern-match.

Truthiness: `Found` is truthy, `Ambiguous` and `NotFound` are falsy. A
caller that only needs the simple "did we find it" answer can keep using
`if not result: return` and unwrap `result.obj` after a `Found` check.
"""

from dataclasses import dataclass, field
from typing import Any


class SearchResult:
    """Base type for search results. Use the concrete subclasses."""

    __slots__ = ()


@dataclass(frozen=True)
class Found(SearchResult):
    """A single match was resolved.

    Attributes:
        obj: The matched object.
        stack: When `stacked=N` was passed and identical objects were
            matched, the full list of matched stack members. `None` for
            normal single matches.
    """

    obj: Any
    stack: list | None = None

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True)
class Ambiguous(SearchResult):
    """Multiple candidates matched and could not be auto-resolved.

    Attributes:
        candidates: All matching objects.
        search_string: The original search query.
        invalid_other: True when the caller used the "other <name>"
            selector but the match count was not exactly two.
    """

    candidates: list
    search_string: str
    invalid_other: bool = False

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class NotFound(SearchResult):
    """No matches were found.

    Attributes:
        search_string: The original search query.
    """

    search_string: str

    def __bool__(self) -> bool:
        return False
