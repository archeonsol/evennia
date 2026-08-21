"""Saved views and operator presence.

Two cross-cutting promises that had nowhere to live. Neither is a subject
domain of its own; both are about the console being usable by more than one
person, on more than one day.

**Saved views** store the address-bar state, not a query. Every panel's view
state is already in the URL, so a saved view is that string plus a name, and it
stays correct as long as the panel reading those keys does. A stored query
would need re-validating against the panel on every load and would drift the
first time a filter was renamed.

**Presence** writes nothing to the database. See :mod:`evennia.console.presence`
for why, and for the limitation that follows from it.

"""

from __future__ import annotations

from django.db import IntegrityError

from evennia.console import audit, presence
from evennia.console.models import ConsoleSavedView
from evennia.console.registry import Panel

#: Views returned at once. A console with more saved views than this has a
#: naming problem rather than a paging problem.
MAX_VIEWS = 200


class ViewsPanel(Panel):
    """Named views, and who else is here."""

    key = "views"
    label = "Saved views"
    description = "Named filter combinations, and the operators currently on the console."
    columns = ("panel", "name", "created_by_name")
    needs_io = False

    def rows(self, ctx):
        """Return the saved views and the current roster.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``panel``.

        Returns:
            dict: Views, pinned views, and who else is present.
        """

        queryset = ConsoleSavedView.objects.all()
        panel = str(ctx.params.get("panel") or "").strip()
        if panel:
            queryset = queryset.filter(panel=panel)

        rows = [
            self._view(row)
            for row in queryset.values(
                "id",
                "name",
                "panel",
                "query",
                "description",
                "pinned",
                "created_by_id",
                "created_by_name",
                "created_at",
            )[:MAX_VIEWS]
        ]
        return {
            "rows": rows,
            "pinned": [row for row in rows if row["pinned"]],
            "present": presence.present(exclude=ctx.actor_id),
            "heartbeat_seconds": presence.HEARTBEAT,
            "presence_note": (
                "The console keeps this list in the cache, not the database, so it "
                "makes no database writes. With the default cache, the list shows "
                "only the operators on this web process."
            ),
            "note": (
                "A saved view stores the address of the view. It works for as long "
                "as the panel uses the same address keys."
            ),
        }

    def _view(self, row):
        """Return one saved view as plain data."""

        return {
            "id": row["id"],
            "name": row["name"],
            "panel": row["panel"],
            "query": row["query"],
            "description": row["description"],
            "pinned": row["pinned"],
            "created_by_id": row["created_by_id"],
            "created_by_name": row["created_by_name"] or "(unknown)",
            "created_at": row["created_at"].isoformat() if row["created_at"] else "",
            "url": f"#{row['panel']}" + (f"?{row['query']}" if row["query"] else ""),
        }

    def save(self, ctx, name=None, panel=None, query="", description="", pinned=False):
        """Create or replace one saved view.

        Worker-side: this writes a console row and touches no game state.

        Args:
            ctx: Worker context.
            name: View name, unique within its panel.
            panel: Panel key the view belongs to.
            query: Address-bar query string for the view.
            description: Optional one-line explanation.
            pinned: Whether to show it in the station rail.

        Returns:
            dict: The stored view.

        Raises:
            ValueError: No name or no panel was given.
        """

        label = str(name or "").strip()
        target = str(panel or "").strip()
        if not label:
            raise ValueError("Enter a name for the view.")
        if not target:
            raise ValueError("Select the panel that the view belongs to.")

        defaults = {
            "query": str(query or "")[:1000],
            "description": str(description or "")[:300],
            "pinned": bool(pinned),
            "created_by_id": ctx.actor_id,
            "created_by_name": str(ctx.actor_name or "")[:255],
        }
        try:
            row, created = ConsoleSavedView.objects.update_or_create(
                panel=target[:64], name=label[:120], defaults=defaults
            )
        except IntegrityError as err:
            raise ValueError(
                f"A view with the name {label!r} exists for the panel {target!r}."
            ) from err

        audit.record(
            panel=self.key,
            operation="add" if created else "change",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"console.consolesavedview#{row.id}"[:160],
            after={"name": row.name, "panel": row.panel, "query": row.query},
            message=f"saved view {row.name!r} on {row.panel}",
        )
        return self._view(
            {
                "id": row.id,
                "name": row.name,
                "panel": row.panel,
                "query": row.query,
                "description": row.description,
                "pinned": row.pinned,
                "created_by_id": row.created_by_id,
                "created_by_name": row.created_by_name,
                "created_at": row.created_at,
            }
        )

    def forget(self, ctx, view_id=None):
        """Delete one saved view.

        Anybody admitted may delete any view. Under decision D1 there is one
        capability, so an ownership check here would be a rule the console
        cannot actually enforce anywhere else -- and the audit row already
        records who did it.

        Args:
            ctx: Worker context.
            view_id: View to delete.

        Returns:
            dict: What was deleted.

        Raises:
            LookupError: No such view.
        """

        try:
            row = ConsoleSavedView.objects.filter(pk=int(view_id)).first()
        except (TypeError, ValueError) as err:
            raise LookupError(f"{view_id!r} is not a valid view id") from err
        if row is None:
            raise LookupError(f"no saved view with id {view_id!r}")

        name, panel = row.name, row.panel
        row.delete()
        audit.record(
            panel=self.key,
            operation="delete",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"console.consolesavedview#{view_id}"[:160],
            before={"name": name, "panel": panel},
            message=f"forgot view {name!r} on {panel}",
        )
        return {"name": name, "panel": panel}

    def heartbeat(self, ctx, panel="", record=""):
        """Report this operator as present, and return who else is.

        Deliberately not audited. A heartbeat every thirty seconds per open
        console would bury every real operation in the trail, and "somebody had
        the console open" is not a fact the audit table exists to hold.

        Args:
            ctx: Worker context.
            panel: Panel key currently open.
            record: Record reference currently open, if any.

        Returns:
            dict: The others present, and the others on this same record.
        """

        presence.touch(ctx.actor_id, ctx.actor_name, panel=panel, record=record)
        return {
            "present": presence.present(exclude=ctx.actor_id),
            "on_this_record": presence.on_record(record, exclude=ctx.actor_id),
            "heartbeat_seconds": presence.HEARTBEAT,
        }

    def depart(self, ctx):
        """Drop this operator from the roster immediately.

        Without it an entry lingers for its time to live, and somebody who left
        five seconds ago still reads as editing the row you are about to edit.
        """

        presence.leave(ctx.actor_id)
        return {"present": presence.present(exclude=ctx.actor_id)}

    def claim(self, ctx, record=""):
        """Return who else has one record open.

        Called before opening an editor. The answer is advice, not a lock: this
        console does not take locks, because a lock nobody can release outlives
        the person who took it. The write path still rejects a stale write as a
        conflict, and this only means the operator finds out first.

        Args:
            ctx: Worker context.
            record: Record reference.

        Returns:
            dict: Who else is on it. ``is_lock`` is always false, so a caller
            cannot mistake this for something that can refuse.
        """

        others = presence.on_record(record, exclude=ctx.actor_id)
        return {
            "record": str(record or ""),
            "others": others,
            "advice": (f"{others[0]['actor_name']} has this record open." if others else ""),
            "is_lock": False,
        }
