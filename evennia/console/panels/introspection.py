"""Objects, Actions, Hooks, and Prototypes.

The panels that make the engine legible. None of them changes anything; all
four exist because this engine's structure lives in Python registries rather
than in the database schema, and until now the only way to read those was to
open the source.

They also share a hazard, and it is worth naming once here because it bit this
module three times while it was written: **every registry has a shape, and
guessing it fails silently.** The hook registry returns records rather than a
mapping. Verbs belong to the action registry rather than to action classes.
``get_all_typeclasses`` scans engine modules only, so membership in it is not a
test of whether a path imports. Each wrong guess produced a panel that rendered
happily while telling the operator something untrue, which is worse than one
that fails.

"""

from __future__ import annotations

from evennia.console import runtime_spec
from evennia.console.registry import Panel, io_action


class ObjectsPanel(Panel):
    """The typeclass tree, and what is stored under each path."""

    key = "objects"
    label = "Objects"
    description = "Typeclasses, how many rows carry each, and paths that no longer import."
    columns = ("path", "instances")
    needs_io = False

    def rows(self, ctx):
        """Return the typeclass tree with stored counts.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``search``.

        Returns:
            dict: Importable typeclasses, stored paths, and orphans.
        """

        search = str(ctx.params.get("search") or "").strip().lower()
        specs = runtime_spec.typeclass_specs()
        rows = [
            item.as_dict()
            for item in specs
            if not search or search in item.path.lower() or search in item.name.lower()
        ]

        stored = runtime_spec.stored_typeclass_counts()
        importable = {item.path for item in specs}
        return {
            "rows": rows,
            "typeclass_count": len(specs),
            "stored": [
                {"path": path, "instances": total, "importable": path in importable}
                for path, total in sorted(stored.items())
                if path
            ],
            "orphans": list(runtime_spec.orphan_typeclass_paths()),
            "note": (
                "Counts group on db_typeclass_path, a real indexed column. A class "
                "reachable under two spellings appears twice with its rows split "
                "between them, which is exactly what a filter written against one "
                "spelling silently misses."
            ),
            "importable_caveat": (
                "The importable list reflects what this process has loaded, so it grows "
                "as the server warms up and is shorter in a fresh one. The stored list "
                "is the reliable half: it is what the database actually holds."
            ),
        }


class ActionsPanel(Panel):
    """Registered actions, their verbs, and what an input resolves to."""

    key = "actions"
    label = "Actions"
    description = "Every registered action, and what one typed line would reach."
    columns = ("name", "verbs")
    needs_io = False

    def rows(self, ctx):
        """Return the action registry.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``search``.
        """

        search = str(ctx.params.get("search") or "").strip().lower()
        data = runtime_spec.action_specs()
        rows = [
            row
            for row in data.get("actions", [])
            if not search
            or search in row["name"].lower()
            or any(search in verb for verb in row["verbs"])
        ]
        return {
            "available": data.get("available", False),
            "reason": data.get("reason", ""),
            "rows": rows,
            "action_count": data.get("action_count", 0),
            "verbs": data.get("verbs", []),
            "note": (
                "Cmdsets are retired; try_action_dispatch is the sole player-input path, "
                "so this is what replaced reading a cmdset tree."
            ),
        }

    @io_action
    def resolve(self, ctx, text=""):
        """Report what one typed line would reach, without running it.

        The question an operator actually has is "why did that not work", and
        answering it by reading the verb trie by hand is the thing this
        replaces. Nothing is executed: only the match is reported.

        Args:
            ctx: IO context.
            text: The input line to resolve.

        Returns:
            dict: The matched action and the verb that matched, or the
            candidates that nearly did.

        Raises:
            ValueError: No input was given.
        """

        line = str(text or "").strip()
        if not line:
            raise ValueError("nothing to resolve")

        try:
            from evennia.actions.registry import action_registry
        except Exception as err:  # noqa: BLE001
            return {"input": line, "available": False, "reason": str(err)[:200]}

        verb = line.split(None, 1)[0]
        matched = None
        try:
            # A ``(verb, action_class, score)`` tuple, not an object. Reading
            # it as one produced a resolver that reported "no match" while
            # suggesting the very verb it had just matched.
            match = action_registry.match_verb(verb)
            if match:
                resolved_verb, action_cls, score = (list(match) + [None, None, None])[:3]
                if action_cls is not None:
                    matched = {
                        "action": getattr(action_cls, "__name__", str(action_cls)),
                        "module": getattr(action_cls, "__module__", ""),
                        "verb": str(resolved_verb or verb),
                        "score": float(score) if score is not None else None,
                    }
        except Exception:  # noqa: BLE001 - no match is an answer, not a fault
            matched = None

        suggestions = []
        if matched is None:
            try:
                suggestions = [str(item) for item in action_registry.suggest_verbs(verb) or ()][:10]
            except Exception:  # noqa: BLE001
                suggestions = []

        return {
            "input": line,
            "verb": verb,
            "available": True,
            "matched": matched,
            "suggestions": suggestions,
            "explanation": (
                f"{verb!r} reaches {matched['action']} (as {matched['verb']!r})."
                if matched
                else f"{verb!r} matches no registered verb."
            ),
        }


class HooksPanel(Panel):
    """The H1 hook registry, and what its lint says about it."""

    key = "hooks"
    label = "Hooks"
    description = "Every declared engine hook, its contract, and the lint findings."
    columns = ("name", "event", "phase", "returns")
    needs_io = False

    def rows(self, ctx):
        """Return the hook contract table and lint findings.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``event`` and
                ``search``.
        """

        params = ctx.params
        event = str(params.get("event") or "").strip().lower()
        search = str(params.get("search") or "").strip().lower()

        data = runtime_spec.hook_specs()
        rows = []
        for row in data.get("hooks", []):
            if event and row["event"].lower() != event:
                continue
            if search and search not in (row["name"] + row["event"]).lower():
                continue
            rows.append(row)

        return {
            "available": data.get("available", False),
            "reason": data.get("reason", ""),
            "rows": rows,
            "hook_count": len(data.get("hooks", [])),
            "events": sorted({row["event"] for row in data.get("hooks", []) if row["event"]}),
            "findings": data.get("findings", []),
            "note": (
                "The registry is descriptive metadata that validates itself, not a "
                "dispatcher: the engine still calls each hook directly."
            ),
        }


class PrototypesPanel(Panel):
    """Prototypes, read-only on purpose."""

    key = "prototypes"
    label = "Prototypes"
    description = "The spawn templates this game declares."
    columns = ("key", "typeclass", "parent")
    needs_io = False

    def rows(self, ctx):
        """Return the declared prototypes.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``search``.
        """

        data = runtime_spec.prototype_specs(search=str(ctx.params.get("search") or ""))
        return {
            "available": data.get("available", False),
            "reason": data.get("reason", ""),
            "rows": data.get("prototypes", []),
            "count": data.get("count", 0),
            "note": (
                "Read-only. Prototypes belong in version-controlled Python modules, so "
                "editing them from the web would reintroduce exactly the unversioned "
                "data the rule exists to prevent."
            ),
        }
