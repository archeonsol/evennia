"""The database panel the JSONB migration made necessary.

``performance.md`` states rules about document size, index cost, and query
shape that nobody could previously check against their own data. This is where
those rules meet the actual tables.

PostgreSQL is what production runs, and every statistic here comes from a
PostgreSQL catalogue view. A development install on SQLite gets a clear
explanation of what is unavailable and why, never an empty table that reads as
"nothing to report".

Everything is a read of a statistics view. No query here scans a data table:
``pg_class.reltuples`` is an estimate maintained by the planner, and asking it
costs the same on a table of ten rows and a table of ten million. The one
exception is the ``db_attrs`` size distribution, which is sampled and says so.

"""

from __future__ import annotations

from django.apps import apps
from django.db import connection

from evennia.console.registry import Panel

#: Rows returned by any listing here.
MAX_ROWS = 100

#: Documents read for the size distribution. Sampled rather than aggregated:
#: ``length(db_attrs::text)`` over a whole table is a sequential scan, and this
#: panel exists partly to discourage exactly that.
SIZE_SAMPLE = 2000

#: Document byte size past which an object taxes every write on itself.
FAT_DOCUMENT_BYTES = 4096

#: Seconds a statement may run before this panel calls it long-running.
LONG_QUERY_SECONDS = 5


def _is_postgres() -> bool:
    """Return whether the default connection is PostgreSQL."""

    return connection.vendor == "postgresql"


def _rows(sql, params=()):
    """Run one read-only catalogue query and return dict rows."""

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


class DatabasePanel(Panel):
    """Table sizes, index usage, connections, and the attribute-size picture."""

    key = "database"
    label = "Database"
    description = "Table and index sizes, index usage, connections, and vacuum state."
    columns = ("table", "rows", "total_bytes")
    needs_io = False

    def rows(self, ctx):
        """Return the database picture.

        Args:
            ctx: Worker context.

        Returns:
            dict: Tables, indexes, connections, and maintenance state.
        """

        if not _is_postgres():
            return {
                "supported": False,
                "vendor": connection.vendor,
                "reason": (
                    f"This server uses {connection.vendor}. This panel reads table "
                    "sizes, index use, connection counts, and vacuum times from "
                    "PostgreSQL system views. Those views do not exist in "
                    f"{connection.vendor}. The production server uses PostgreSQL."
                ),
                "tables": [],
                "indexes": [],
                "connections": {},
                "long_running": [],
                "unused_indexes": [],
            }

        return {
            "supported": True,
            "vendor": connection.vendor,
            "tables": self._tables(),
            "indexes": self._indexes(),
            "unused_indexes": self._unused_indexes(),
            "connections": self._connections(),
            "long_running": self._long_running(),
            "note": (
                "These row counts are estimates, not exact counts. PostgreSQL "
                "updates them when it runs ANALYZE. A table with many recent "
                "writes shows a low count."
            ),
        }

    def _tables(self):
        """Return per-table size and estimated row count."""

        return _rows(
            """
            SELECT
                relname AS table,
                n_live_tup AS rows,
                pg_total_relation_size(relid) AS total_bytes,
                pg_relation_size(relid) AS heap_bytes,
                pg_indexes_size(relid) AS index_bytes,
                n_dead_tup AS dead_rows,
                last_vacuum,
                last_autovacuum,
                last_analyze,
                last_autoanalyze
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT %s
            """,
            [MAX_ROWS],
        )

    def _indexes(self):
        """Return index usage, so an index can be shown to earn its write cost."""

        return _rows(
            """
            SELECT
                relname AS table,
                indexrelname AS index,
                idx_scan AS scans,
                idx_tup_read AS tuples_read,
                pg_relation_size(indexrelid) AS bytes
            FROM pg_stat_user_indexes
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT %s
            """,
            [MAX_ROWS],
        )

    def _unused_indexes(self):
        """Return indexes never scanned since statistics were last reset.

        An index costs a write on every insert and update of its table. One
        that has never been scanned is paying that and returning nothing, which
        is a finding rather than a statistic -- but only against a statistics
        window long enough to be meaningful, which is why the reset time is
        reported alongside it.
        """

        found = _rows(
            """
            SELECT
                relname AS table,
                indexrelname AS index,
                pg_relation_size(indexrelid) AS bytes
            FROM pg_stat_user_indexes
            WHERE idx_scan = 0
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT %s
            """,
            [MAX_ROWS],
        )
        reset = _rows("SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()")
        return {
            "rows": found,
            "stats_reset": str(reset[0]["stats_reset"]) if reset else "",
            "note": (
                "PostgreSQL counts scans from the time it last reset the "
                "statistics. After a recent reset, every index looks unused."
            ),
        }

    def _connections(self):
        """Return connection counts by state, against the configured ceiling."""

        by_state = _rows(
            """
            SELECT COALESCE(state, 'unknown') AS state, COUNT(*) AS total
            FROM pg_stat_activity
            WHERE datname = current_database()
            GROUP BY state
            ORDER BY state
            """
        )
        limit = _rows("SHOW max_connections")
        total = sum(int(row["total"]) for row in by_state)
        ceiling = int(limit[0]["max_connections"]) if limit else 0
        return {
            "by_state": by_state,
            "total": total,
            "max_connections": ceiling,
            "headroom": max(0, ceiling - total),
        }

    def _long_running(self):
        """Return statements running longer than the attention threshold."""

        return _rows(
            """
            SELECT
                pid,
                state,
                EXTRACT(EPOCH FROM (now() - query_start))::int AS seconds,
                LEFT(query, 300) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND state <> 'idle'
              AND query_start IS NOT NULL
              AND now() - query_start > %s * INTERVAL '1 second'
            ORDER BY query_start
            LIMIT %s
            """,
            [LONG_QUERY_SECONDS, MAX_ROWS],
        )

    def sizes(self, ctx, model=None):
        """Return the ``db_attrs`` document-size distribution for one model.

        ``performance.md`` says a large rarely-read value taxes every small
        frequently-written one on the same object, because a flush serializes
        the whole document. This is that rule measured.

        Sampled rather than aggregated. ``length(db_attrs::text)`` across a
        whole table is a sequential scan, and a panel that exists partly to
        discourage table scans should not open with one.

        Args:
            ctx: Worker context carrying ``model``.

        Returns:
            dict: The distribution, and the largest documents in the sample.
        """

        label = str(model or "").strip()
        if not label:
            raise LookupError("a model label is required")
        target = apps.get_model(label)
        if not any(field.name == "db_attrs" for field in target._meta.concrete_fields):
            raise LookupError(f"{label} carries no attribute document")

        if not _is_postgres():
            documents = list(
                target._base_manager.order_by("-id").values("id", "db_attrs")[:SIZE_SAMPLE]
            )
            measured = [
                {"id": row["id"], "bytes": len(str(row["db_attrs"] or {}))} for row in documents
            ]
        else:
            table = target._meta.db_table
            measured = [
                {"id": row["id"], "bytes": int(row["bytes"])}
                for row in _rows(
                    f'SELECT id, length(db_attrs::text) AS bytes FROM "{table}" '  # noqa: S608
                    "ORDER BY id DESC LIMIT %s",
                    [SIZE_SAMPLE],
                )
            ]

        measured.sort(key=lambda row: row["bytes"], reverse=True)
        sizes = [row["bytes"] for row in measured]
        fat = [row for row in measured if row["bytes"] > FAT_DOCUMENT_BYTES]
        return {
            "model": label,
            "sampled": len(measured),
            "complete": len(measured) < SIZE_SAMPLE,
            "largest": measured[:25],
            "fat_count": len(fat),
            "threshold_bytes": FAT_DOCUMENT_BYTES,
            "median_bytes": sizes[len(sizes) // 2] if sizes else 0,
            "total_bytes": sum(sizes),
            "note": (
                "The server writes the full document each time one attribute "
                "changes. A large value makes every small change more expensive."
            ),
        }

    def models(self, ctx):
        """Return the models that carry an attribute document."""

        from evennia.console.panels.attributes import attribute_models

        return {"models": list(attribute_models())}
