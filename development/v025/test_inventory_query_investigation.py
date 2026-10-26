"""Locate query failures precisely, without PostgreSQL, SQL execution or sockets."""
import unittest
from unittest.mock import AsyncMock, patch

import asyncpg

import integration_support as infra
from integration_catalog import INVENTORY_SQL
from integration_diagnostics import SetupCategory, SetupStage, setup_category
from integration_manifest import reviewed_table_definitions
from test_integration_setup_diagnostics import TransactionTraceDouble
from test_postgres_setup_compatibility import oid_json_inventory
from test_postgres_suite_safety import config


class QueryFailureBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unclassified_server_and_python_failures_match_the_observed_signature(self):
        import tempfile
        from pathlib import Path
        for error in (asyncpg.InternalServerError('private'), asyncpg.FeatureNotSupportedError('private'),
                      asyncpg.InvalidSchemaNameError('private'),
                      asyncpg.DataError('private'), RuntimeError('private'), TypeError('private'),
                      infra.IntegrationSafetyError('private')):
            with self.subTest(kind=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                con = TransactionTraceDouble()
                original = con._fetch
                async def failing_fetch(sql, *args):
                    if sql == INVENTORY_SQL:
                        raise error
                    return await original(sql, *args)
                con.fetch.side_effect = failing_fetch
                with patch.object(infra.asyncpg, 'connect', AsyncMock(return_value=con)), patch.object(
                        infra.IsolatedTestDatabase, '_validate_initial_inventory') as validate:
                    with self.assertRaises(infra.IntegrationSafetyError) as raised:
                        await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                            expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                            allow_database_writes=True, recovery_directory=directory)
                diagnostic = infra.setup_failure_diagnostic(raised.exception)
                diagnostic.pop('inventory_fetch')  # New metadata does not rewrite the historical signature.
                self.assertEqual(diagnostic, dict(category='SAFETY', stage='INVENTORY_QUERY',
                    statement_ordinal=0, table=None, rollback='ACKNOWLEDGED',
                    rollback_verification='UNKNOWN', connection_cleanup='ACKNOWLEDGED'))
                self.assertEqual(con.attempts, 27)
                self.assertEqual((con.commits, con.rollbacks), (0, 1))
                validate.assert_not_called()
                self.assertEqual(raised.exception.recovery.table_oids, ())
                self.assertIsNone(raised.exception.recovery.inventory_digest)
                self.assertTrue((Path(directory) / (con.schema + '.json')).exists())
                self.assertFalse(any(event == 'execute' and sql.startswith('DROP ')
                                     for event, sql in con.events))

    async def test_invalid_inventory_rows_fail_only_in_inventory_validation(self):
        import tempfile
        con = TransactionTraceDouble()
        original = con._fetch
        async def bad_rows(sql, *args):
            rows = await original(sql, *args)
            if sql == INVENTORY_SQL:
                rows = oid_json_inventory(rows, records=False)
                rows[0]['owner'] = 99999
            return rows
        con.fetch.side_effect = bad_rows
        with tempfile.TemporaryDirectory() as directory, patch.object(
                infra.asyncpg, 'connect', AsyncMock(return_value=con)):
            with self.assertRaises(infra.IntegrationSafetyError) as raised:
                await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                    expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                    allow_database_writes=True, recovery_directory=directory)
        diagnostic = infra.setup_failure_diagnostic(raised.exception)
        self.assertEqual(diagnostic['stage'], 'INVENTORY_VALIDATION')
        self.assertEqual(diagnostic['category'], 'SAFETY')

    async def test_bad_search_path_rejects_before_any_create_or_inventory_fetch(self):
        import tempfile
        con = TransactionTraceDouble('path')
        with tempfile.TemporaryDirectory() as directory, patch.object(
                infra.asyncpg, 'connect', AsyncMock(return_value=con)):
            with self.assertRaises(infra.IntegrationSafetyError) as raised:
                await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                    expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                    allow_database_writes=True, recovery_directory=directory)
        self.assertEqual(infra.setup_failure_diagnostic(raised.exception)['stage'], 'SEARCH_PATH_SETUP')
        self.assertEqual(con.attempts, 0)
        self.assertFalse(any(call.args[0] == INVENTORY_SQL for call in con.fetch.await_args_list))

    def test_classification_distinguishes_known_query_errors_from_safety_fallback(self):
        mapped = ((asyncpg.InsufficientPrivilegeError, SetupCategory.PRIVILEGES),
                  (asyncpg.ReadOnlySQLTransactionError, SetupCategory.READ_ONLY),
                  (asyncpg.UndefinedColumnError, SetupCategory.SQL_COMPATIBILITY),
                  (asyncpg.InFailedSQLTransactionError, SetupCategory.TRANSACTION),
                  (asyncpg.SerializationError, SetupCategory.TRANSACTION),
                  (asyncpg.InvalidPasswordError, SetupCategory.AUTHENTICATION),
                  (asyncpg.ConnectionDoesNotExistError, SetupCategory.CONNECTION),
                  (asyncpg.QueryCanceledError, SetupCategory.TIMEOUT),
                  (asyncpg.LockNotAvailableError, SetupCategory.TIMEOUT),
                  (asyncpg.InterfaceError, SetupCategory.CONNECTION),
                  (OSError, SetupCategory.CONNECTION), (TimeoutError, SetupCategory.TIMEOUT))
        for kind, expected in mapped:
            with self.subTest(kind=kind):
                self.assertEqual(setup_category(kind('private'), SetupStage.INVENTORY_QUERY), expected)

    def test_current_manifest_and_postgresql18_contract_still_match(self):
        self.assertEqual(len(reviewed_table_definitions()), 27)
        self.assertIn("'pg_catalog.pg_namespace'::regclass::oid", INVENTORY_SQL)
        self.assertIn('relnamespace=$1::oid', INVENTORY_SQL)
        self.assertIn('k.conkey', INVENTORY_SQL)
        self.assertIn("'conenforced'", INVENTORY_SQL)
        self.assertIn('child.classid=d.classid AND child.oid=d.objid', INVENTORY_SQL)


if __name__ == '__main__':
    unittest.main()
