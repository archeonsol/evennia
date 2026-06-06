"""MuxCommand/ObjManipCommand-style argument parsing for the action engine.

The action parser strips the verb and switches; this splits the remaining
free-text args the way Evennia's command classes do, so actions hosting former
mux/objmanip commands can carry lhs/rhs/objdef structure.

The split mirrors ``Command.parse`` (lhs/rhs/lhslist/rhslist) and
``ObjManipCommand.parse`` (the per-objdef alias/option breakdown). Switch
extraction is intentionally *not* duplicated here: the action parser has already
consumed the verb and its ``/switches`` by the time this runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["MuxArgs", "mux_parse"]


@dataclass
class MuxArgs:
    """The structured breakdown of one action's free-text arguments.

    Attributes:
        args (str): the stripped argument string (verb and switches already
            removed by the action parser).
        arglist (list): ``args`` split on whitespace.
        lhs (str): left of the first delimiter (or all of ``args`` when absent).
        rhs (str | None): right of the first delimiter, or ``None`` when no
            delimiter is present.
        lhslist (list): ``lhs`` split on commas.
        rhslist (list): ``rhs`` split on commas (empty when ``rhs`` is ``None``).
        lhs_objs (list): per-comma objdef dicts from ``lhs``, each
            ``{"name": str, "option": str | None, "aliases": list}``.
        rhs_objs (list): the same breakdown for ``rhs``.
    """

    args: str = ""
    arglist: list = field(default_factory=list)
    lhs: str = ""
    rhs: str | None = None
    lhslist: list = field(default_factory=list)
    rhslist: list = field(default_factory=list)
    lhs_objs: list = field(default_factory=list)
    rhs_objs: list = field(default_factory=list)


def mux_parse(raw_args: str, rhs_split: str | tuple | list = "=") -> MuxArgs:
    """Split free-text action args into lhs/rhs/objdef structure.

    Args:
        raw_args (str): the argument text remaining after the action parser has
            stripped the verb and any ``/switches``.
        rhs_split (str | tuple | list): the lhs/rhs delimiter. A single string
            (default ``"="``) splits on that string. An iterable of strings is
            tried in order; the first delimiter present in the input wins,
            matching ``Command.rhs_split``.

    Returns:
        MuxArgs: the populated breakdown. ``rhs`` is ``None`` (and ``rhslist``
            empty) when no delimiter is found.

    Notes:
        Each comma-separated objdef on a side is parsed as
        ``name;alias;alias:option`` -> ``{"name", "aliases", "option"}``,
        matching ``ObjManipCommand`` (the ``:`` option is split off first, then
        the ``;`` alias list).
    """
    args = (raw_args or "").strip()
    arglist = [a.strip() for a in args.split()]

    lhs, rhs = args, None
    if lhs:
        if isinstance(rhs_split, (tuple, list)):
            best_split = next((d for d in rhs_split if d in lhs), None)
        else:
            best_split = rhs_split if rhs_split in lhs else None
        if best_split:
            lhs, rhs = lhs.split(best_split, 1)
    lhs = lhs.strip()
    rhs = rhs.strip() if rhs is not None else None

    lhslist = [a.strip() for a in lhs.split(",")]
    rhslist = [a.strip() for a in rhs.split(",")] if rhs is not None else []

    obj_defs = ([], [])
    for iside, sidelist in enumerate((lhslist, rhslist)):
        for objdef in sidelist:
            aliases, option = [], None
            if ":" in objdef:
                objdef, option = [p.strip() for p in objdef.rsplit(":", 1)]
            if ";" in objdef:
                objdef, alias_str = [p.strip() for p in objdef.split(";", 1)]
                aliases = [a.strip() for a in alias_str.split(";") if a.strip()]
            obj_defs[iside].append({"name": objdef, "option": option, "aliases": aliases})

    return MuxArgs(args, arglist, lhs, rhs, lhslist, rhslist, obj_defs[0], obj_defs[1])
