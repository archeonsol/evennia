"""Tests for ``evennia.server.database_postgres``.

Cover the regression where ``statement_timeout`` was injected via
``OPTIONS['options'] = '-c ...'``, which PgBouncer transaction-pool mode
rejects at the protocol level. The session tunables now flow through a
``connection_created`` receiver.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from evennia.server import database_postgres as dbpg


class TestApplyPostgresEngineDefaults(SimpleTestCase):
    """``apply_postgres_engine_defaults`` no longer injects an ``options`` startup parameter."""

    def _pg(self, **extra):
        cfg = {"ENGINE": "django.db.backends.postgresql"}
        cfg.update(extra)
        return cfg

    def test_no_options_startup_parameter_injected(self):
        out = dbpg.apply_postgres_engine_defaults({"default": self._pg()})
        opts = out["default"].get("OPTIONS", {})
        self.assertNotIn(
            "options",
            opts,
            "options startup parameter must not be set — PgBouncer rejects it.",
        )

    def test_conn_max_age_and_health_checks_applied(self):
        with self.settings(
            ENGINE_DATABASE_CONN_MAX_AGE=600,
            ENGINE_DATABASE_CONN_HEALTH_CHECKS=True,
            ENGINE_DATABASE_TRANSACTION_POOLING=False,
        ):
            out = dbpg.apply_postgres_engine_defaults({"default": self._pg()})
        self.assertEqual(out["default"]["CONN_MAX_AGE"], 600)
        self.assertTrue(out["default"]["CONN_HEALTH_CHECKS"])

    def test_transaction_pooling_disables_server_cursors_and_persistence(self):
        with self.settings(
            ENGINE_DATABASE_TRANSACTION_POOLING=True,
            ENGINE_DATABASE_CONN_MAX_AGE=600,
        ):
            out = dbpg.apply_postgres_engine_defaults(
                {
                    "default": self._pg(
                        CONN_MAX_AGE=600,
                        DISABLE_SERVER_SIDE_CURSORS=False,
                    )
                }
            )

        self.assertEqual(out["default"]["CONN_MAX_AGE"], 0)
        self.assertTrue(out["default"]["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertTrue(out["default"]["CONN_HEALTH_CHECKS"])

    def test_non_postgres_aliases_untouched(self):
        sqlite_cfg = {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        out = dbpg.apply_postgres_engine_defaults({"default": sqlite_cfg})
        self.assertEqual(out["default"], sqlite_cfg)
        self.assertNotIn("CONN_MAX_AGE", out["default"])


class TestBuildReadReplicaEntry(SimpleTestCase):
    """``build_read_replica_entry`` registers the alias for read-only enforcement."""

    def setUp(self):
        # don't leak registrations across tests
        self._saved = set(dbpg._READ_REPLICA_ALIASES)
        dbpg._READ_REPLICA_ALIASES.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        dbpg._READ_REPLICA_ALIASES.clear()
        dbpg._READ_REPLICA_ALIASES.update(self._saved)

    def test_no_options_startup_parameter_injected(self):
        primary = {"ENGINE": "django.db.backends.postgresql"}
        cfg = dbpg.build_read_replica_entry(primary, name="reporting")
        opts = cfg.get("OPTIONS", {})
        self.assertNotIn("options", opts)

    def test_registers_alias_for_read_only_enforcement(self):
        primary = {"ENGINE": "django.db.backends.postgresql"}
        dbpg.build_read_replica_entry(primary, name="reporting")
        self.assertIn("reporting", dbpg._READ_REPLICA_ALIASES)


class TestConnectionCreatedReceiver(SimpleTestCase):
    """``_apply_engine_pg_session_init`` issues ``SET`` after the handshake."""

    def setUp(self):
        self._saved = set(dbpg._READ_REPLICA_ALIASES)
        dbpg._READ_REPLICA_ALIASES.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        dbpg._READ_REPLICA_ALIASES.clear()
        dbpg._READ_REPLICA_ALIASES.update(self._saved)

    def _fake_connection(self, *, vendor="postgresql", alias="default"):
        conn = MagicMock()
        conn.vendor = vendor
        conn.alias = alias
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        return conn, cursor

    def test_default_alias_sets_statement_timeout(self):
        conn, cursor = self._fake_connection(alias="default")
        with self.settings(ENGINE_DATABASE_STATEMENT_TIMEOUT_MS=30000):
            dbpg._apply_engine_pg_session_init(sender=None, connection=conn)
        cursor.execute.assert_any_call("SET statement_timeout = 30000")

    def test_non_default_alias_does_not_set_statement_timeout(self):
        conn, cursor = self._fake_connection(alias="reporting")
        with self.settings(ENGINE_DATABASE_STATEMENT_TIMEOUT_MS=30000):
            dbpg._apply_engine_pg_session_init(sender=None, connection=conn)
        for call in cursor.execute.call_args_list:
            self.assertNotIn("statement_timeout", call.args[0])

    def test_zero_timeout_skips_set_statement_timeout(self):
        conn, cursor = self._fake_connection(alias="default")
        with self.settings(ENGINE_DATABASE_STATEMENT_TIMEOUT_MS=0):
            dbpg._apply_engine_pg_session_init(sender=None, connection=conn)
        conn.cursor.assert_not_called()

    def test_registered_replica_alias_sets_read_only(self):
        dbpg._READ_REPLICA_ALIASES.add("reporting")
        conn, cursor = self._fake_connection(alias="reporting")
        with self.settings(ENGINE_DATABASE_STATEMENT_TIMEOUT_MS=0):
            dbpg._apply_engine_pg_session_init(sender=None, connection=conn)
        cursor.execute.assert_any_call("SET default_transaction_read_only = on")

    def test_non_postgres_vendor_skipped(self):
        conn, _ = self._fake_connection(vendor="sqlite", alias="default")
        with self.settings(ENGINE_DATABASE_STATEMENT_TIMEOUT_MS=30000):
            dbpg._apply_engine_pg_session_init(sender=None, connection=conn)
        conn.cursor.assert_not_called()
