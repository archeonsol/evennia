"""Runtime introspection: typeclasses, hooks, actions, and prototypes.

The model half lives in :mod:`evennia.console.spec`. This is the other five
surfaces the plan named, kept separate because they reflect entirely different
things: ``spec`` reads the Django app registry, and this reads the engine's own
registries at runtime.

Everything here is read-only and worker-safe. It inspects classes and
registries rather than instances, so it holds to the boundary rules and keeps
working with the game server down.

"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps


@dataclass(frozen=True, slots=True)
class TypeclassSpec:
    """One typeclass as the console sees it.

    Attributes:
        path: Dotted import path, matching ``db_typeclass_path``.
        name: Class name.
        base: The immediate parent this class descends from.
        parents: Every immediate parent path.
        instances: Rows currently carrying this exact path, or ``None`` when
            counts were not requested.
    """

    path: str
    name: str
    base: str = ""
    parents: tuple[str, ...] = ()
    instances: int | None = None

    def as_dict(self) -> dict:
        """Return the spec as plain JSON-safe data."""

        return {
            "path": self.path,
            "name": self.name,
            "base": self.base,
            "parents": list(self.parents),
            "instances": self.instances,
        }


def stored_typeclass_counts() -> dict:
    """Return how many rows carry each ``db_typeclass_path``.

    Grouped on a real indexed column, so this is exact and cheap. It is also
    the honest way to see the dual-spelling hazard ``performance.md``
    documents: a class reachable under two paths appears here as two entries
    with the rows split between them, which is exactly the failure a filter
    written against one spelling silently produces.
    """

    from django.db.models import Count

    counts = {}
    for model in apps.get_models():
        if model._meta.proxy:
            continue
        if not any(field.name == "db_typeclass_path" for field in model._meta.concrete_fields):
            continue
        try:
            rows = (
                model._base_manager.values("db_typeclass_path")
                .annotate(total=Count("pk"))
                .order_by()
            )
            for row in rows:
                path = row["db_typeclass_path"] or ""
                counts[path] = counts.get(path, 0) + row["total"]
        except Exception:  # noqa: BLE001 - one unreadable model must not break it
            continue
    return counts


def typeclass_specs(counts: bool = True) -> tuple[TypeclassSpec, ...]:
    """Return every importable typeclass, optionally with instance counts.

    Args:
        counts: Whether to count stored instances per path.

    Returns:
        tuple[TypeclassSpec, ...]: Specs in path order.
    """

    from evennia.utils.utils import get_all_typeclasses

    try:
        found = get_all_typeclasses()
    except Exception:  # noqa: BLE001 - a listing must not break the page
        return ()

    stored = stored_typeclass_counts() if counts else {}
    specs = []
    for path, cls in sorted(found.items()):
        parents = tuple(
            f"{parent.__module__}.{parent.__name__}"
            for parent in getattr(cls, "__bases__", ())
            if parent is not object
        )
        specs.append(
            TypeclassSpec(
                path=path,
                name=getattr(cls, "__name__", str(path).rsplit(".", 1)[-1]),
                base=parents[0] if parents else "",
                parents=parents,
                instances=stored.get(path) if counts else None,
            )
        )
    return tuple(specs)


def orphan_typeclass_paths() -> tuple[dict, ...]:
    """Return stored paths that no longer import.

    A row whose ``db_typeclass_path`` names a class that has moved or been
    deleted still loads through whatever fallback the engine applies, and
    nothing announces it. This is the only place that difference is visible.

    Importability is tested by actually importing, not by membership in
    ``get_all_typeclasses()``. That function scans engine modules only, so a
    game's own typeclasses are absent from it while importing perfectly well --
    and a check built on it reports every game typeclass as an orphan, which is
    worse than not checking.
    """

    from evennia.utils.utils import class_from_module

    orphans = []
    for path, total in sorted(stored_typeclass_counts().items()):
        if not path:
            continue
        try:
            class_from_module(path)
        except Exception:  # noqa: BLE001 - any import failure is the finding
            orphans.append({"path": path, "instances": total})
    return tuple(orphans)


def hook_specs() -> dict:
    """Return the H1 hook registry and its lint findings.

    A third consumer of ``evennia.hooks``, alongside the doc generator and the
    startup lint, reading the public API rather than the registry dict so the
    three cannot drift apart.
    """

    try:
        from evennia import hooks
    except Exception:  # noqa: BLE001
        return {"available": False, "hooks": [], "findings": [], "reason": "no hook registry"}

    entries = []
    try:
        # A list of HookSpec records, not a mapping. A HookSpec carries no name
        # of its own: what identifies one is the call site it fires from, so
        # that is what the table is keyed on.
        for spec in hooks.list_all() or ():
            fires_from = [str(item) for item in (getattr(spec, "fires_from", ()) or ())]
            entries.append(
                {
                    "name": fires_from[0] if fires_from else str(getattr(spec, "event", "")),
                    "event": str(getattr(spec, "event", "") or ""),
                    "phase": str(getattr(spec, "phase", "") or ""),
                    "actor": str(getattr(spec, "actor", "") or ""),
                    "returns": str(getattr(spec, "returns", "") or ""),
                    "discipline": str(getattr(spec, "discipline", "") or ""),
                    "fires_from": fires_from,
                    "notes": str(getattr(spec, "notes", "") or "")[:400],
                }
            )
    except Exception as err:  # noqa: BLE001
        return {"available": False, "hooks": [], "findings": [], "reason": str(err)[:200]}

    findings = []
    try:
        for finding in hooks.lint() or ():
            findings.append(
                {
                    "name": str(getattr(finding, "name", finding))[:160],
                    "problem": str(
                        getattr(finding, "problem", None) or getattr(finding, "message", "")
                    )[:300],
                }
            )
    except Exception:  # noqa: BLE001 - a lint failure is a finding, not a crash
        findings = []

    return {
        "available": True,
        "hooks": sorted(entries, key=lambda row: row["name"]),
        "findings": findings,
    }


def action_specs() -> dict:
    """Return every registered action and the verbs that reach it.

    Cmdsets are retired and ``try_action_dispatch`` is the sole player-input
    path, so this is what replaced reading a cmdset tree.
    """

    try:
        from evennia.actions import registry as action_registry_module
    except Exception:  # noqa: BLE001
        return {"available": False, "actions": [], "verbs": []}

    registry = None
    for name in ("action_registry", "ACTION_REGISTRY", "registry", "REGISTRY"):
        candidate = getattr(action_registry_module, name, None)
        if candidate is not None and hasattr(candidate, "all_actions"):
            registry = candidate
            break
    if registry is None:
        return {
            "available": False,
            "actions": [],
            "verbs": [],
            "reason": "the action registry is not exposed under a known name",
        }

    try:
        actions = registry.all_actions()
        verbs = sorted(str(verb) for verb in registry.verbs())
    except Exception as err:  # noqa: BLE001
        return {"available": False, "actions": [], "verbs": [], "reason": str(err)[:200]}

    entries = []
    for action_cls in actions:
        doc = (getattr(action_cls, "__doc__", "") or "").strip().splitlines()
        entries.append(
            {
                "name": getattr(action_cls, "__name__", str(action_cls)),
                "module": getattr(action_cls, "__module__", ""),
                # The registry owns the verb mapping; an action class carries
                # no verbs of its own, so reading them off the class yields an
                # empty list for every action.
                "verbs": _verbs_for(registry, action_cls),
                "capability": str(getattr(action_cls, "capability", "") or ""),
                "summary": doc[0][:200] if doc else "",
            }
        )
    return {
        "available": True,
        "actions": sorted(entries, key=lambda row: row["name"]),
        "verbs": verbs,
        "action_count": len(entries),
    }


def _verbs_for(registry, action_cls):
    """Return the verbs the registry routes to one action."""

    getter = getattr(registry, "verbs_for", None)
    if callable(getter):
        try:
            return sorted(str(verb) for verb in getter(action_cls) or ())
        except Exception:  # noqa: BLE001
            return []
    mapping = getattr(registry, "_verbs_by_action", None) or {}
    try:
        return sorted(str(verb) for verb in mapping.get(action_cls, ()) or ())
    except Exception:  # noqa: BLE001
        return []


def prototype_specs(search: str = "") -> dict:
    """Return the prototypes this game declares.

    Read-only by design. ``performance.md`` requires prototypes to live in
    version-controlled Python modules, so editing them from the web would
    reintroduce the unversioned-data problem that rule exists to prevent.
    """

    try:
        from evennia.prototypes.prototypes import search_prototype
    except Exception:  # noqa: BLE001
        return {"available": False, "prototypes": [], "count": 0}

    try:
        found = search_prototype(key=str(search or "") or None)
    except Exception as err:  # noqa: BLE001
        return {"available": False, "prototypes": [], "count": 0, "reason": str(err)[:200]}

    entries = []
    for prototype in found or ():
        if not isinstance(prototype, dict):
            continue
        entries.append(
            {
                "key": str(prototype.get("prototype_key", ""))[:120],
                "parent": str(prototype.get("prototype_parent", "") or "")[:160],
                "typeclass": str(prototype.get("typeclass", "") or "")[:200],
                "tags": [str(tag)[:60] for tag in (prototype.get("tags") or [])][:20],
                "desc": str(prototype.get("prototype_desc", "") or "")[:300],
                "fields": sorted(
                    str(key) for key in prototype if not str(key).startswith("prototype_")
                ),
            }
        )
    return {
        "available": True,
        "prototypes": sorted(entries, key=lambda row: row["key"]),
        "count": len(entries),
    }
