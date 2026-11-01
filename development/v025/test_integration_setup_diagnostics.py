"""Permanent offline setup/rollback diagnostics. All driver calls use doubles."""
import asyncio
import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from asyncpg.transaction import Transaction, TransactionState
import integration_support as infra
import integration_runner as runner
from integration_catalog import INVENTORY_SQL, PATH_SQL
from test_postgres_suite_safety import PreparedConnectionDouble, config

class TransactionTraceDouble(PreparedConnectionDouble):
    """Record driver transaction calls only; never execute SQL or model durability."""
    def __init__(self, fail_at=None, rollback_error=False):
        super().__init__()
        self.fail_at = fail_at
        self.rollback_error = rollback_error
        self.attempts = 0
        self.rollbacks = 0
        self.commits = 0
        self._pool_release_ctr = 0
        self._top_xact = None
        self._protocol = SimpleNamespace(is_in_transaction=lambda: False)
        self.is_closed = Mock(return_value=False)
        self.transactions = []
        self.transaction = Mock(side_effect=self.new_transaction)

    def new_transaction(self):
        tx = Transaction(self, None, False, False)
        self.transactions.append(tx)
        return tx

    async def _execute(self, sql, *args):
        if sql == 'BEGIN;':
            return 'RECORDED'
        if sql == 'ROLLBACK;':
            self.rollbacks += 1
            if self.rollback_error:
                raise RuntimeError('synthetic-private-rollback-detail')
            return 'RECORDED'
        if sql == 'COMMIT;':
            self.commits += 1
            if self.fail_at == 'commit':
                raise RuntimeError('synthetic-private-commit-detail')
            return 'RECORDED'
        if sql.startswith('CREATE TABLE '):
            self.attempts += 1
            if self.fail_at == self.attempts:
                raise RuntimeError('synthetic-private-ddl-detail')
        return await super()._execute(sql, *args)

    async def _fetchrow(self, sql, *args):
        if sql == PATH_SQL and self.fail_at == 'path':
            return {'current_schema': None, 'schemas': [], 'temp_schema': 0}
        return await super()._fetchrow(sql, *args)

    async def _fetch(self, sql, *args):
        if sql == INVENTORY_SQL and self.fail_at == 'inventory_query':
            raise RuntimeError('synthetic-private-catalog-detail')
        rows = await super()._fetch(sql, *args)
        if sql == INVENTORY_SQL and self.fail_at == 'inventory_validation':
            rows[0]['dependencies_safe'] = False
        return rows

class SetupDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def trace_failure(self, fail_at, rollback_error=False):
        con = TransactionTraceDouble(fail_at, rollback_error)
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(
                        config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity(
                            '172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
            record = raised.exception.recovery
            disk = json.loads((Path(directory) / (con.schema + '.json')).read_text())
        self.assertNotIn('synthetic-private', str(raised.exception))
        self.assertEqual(record.phase, disk['phase'])
        self.assertEqual(record.outcome, disk['outcome'])
        self.assertFalse(any(sql.startswith('DROP ') for event, sql in con.events if event == 'execute'))
        con.close.assert_awaited_once()
        return con, record

    def assert_unverified(self, record):
        self.assertEqual((record.phase, record.outcome), ('creating_tables', 'rollback_unverified'))
        self.assertEqual(record.table_oids, ())
        self.assertIsNone(record.inventory_digest)
        self.assertEqual(record.diagnostic['rollback_verification'], 'UNKNOWN')

    async def test_path_rejection_matches_report_without_any_ddl(self):
        con, record = await self.trace_failure('path')
        self.assert_unverified(record)
        self.assertEqual(con.attempts, 0)

    async def test_first_create_failure_matches_report(self):
        con, record = await self.trace_failure(1)
        self.assert_unverified(record)
        self.assertEqual(con.attempts, 1)

    async def test_late_create_failure_also_has_empty_table_oid_list(self):
        con, record = await self.trace_failure(3)
        self.assert_unverified(record)
        self.assertEqual(con.attempts, 3)

    async def test_inventory_query_failure_matches_report_after_all_creates(self):
        con, record = await self.trace_failure('inventory_query')
        self.assert_unverified(record)
        self.assertEqual(con.attempts, len(infra._table_definitions()))

    async def test_inventory_validation_failure_matches_report_after_all_creates(self):
        con, record = await self.trace_failure('inventory_validation')
        self.assert_unverified(record)
        self.assertEqual(con.attempts, len(infra._table_definitions()))

    async def test_acknowledged_driver_rollback_stays_unverified(self):
        con, record = await self.trace_failure(1)
        self.assert_unverified(record)
        self.assertEqual(con.rollbacks, 1)
        self.assertIs(con.transactions[0]._state, TransactionState.ROLLEDBACK)

    async def test_failed_driver_rollback_has_same_journal_outcome(self):
        con, record = await self.trace_failure(1, rollback_error=True)
        self.assert_unverified(record)
        self.assertEqual(con.rollbacks, 1)
        self.assertIs(con.transactions[0]._state, TransactionState.FAILED)

    async def test_commit_error_uses_distinct_unknown_commit_checkpoint(self):
        con, record = await self.trace_failure('commit')
        self.assertEqual((record.phase, record.outcome), ('committing_setup', 'commit_unknown'))
        self.assertEqual(len(record.table_oids), len(infra._table_definitions()))
        self.assertIsNotNone(record.inventory_digest)
        self.assertEqual((con.commits, con.rollbacks), (1, 0))

    async def test_existing_journal_blocks_replay_before_connection_and_is_unchanged(self):
        from integration_recovery import RecoveryJournal, RecoveryRecord
        con = TransactionTraceDouble()
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            record = RecoveryRecord(con.schema, phase='creating_tables',
                outcome='rollback_unverified', namespace_oid=20000, owner_oid=1000)
            journal = RecoveryJournal(directory, record)
            before = journal.path.read_bytes()
            with patch('integration_support.asyncpg.connect', AsyncMock()) as driver:
                with self.assertRaises(infra.IntegrationSafetyError):
                    await infra.IsolatedTestDatabase.adopt_empty(
                        config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity(
                            '172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
            driver.assert_not_called()
            self.assertEqual(journal.path.read_bytes(), before)

    async def test_failed_adoption_never_runs_cases_or_runner_cleanup(self):
        import postgres_cases
        cases = tuple((name, AsyncMock()) for name in runner.CASE_NAMES)
        plan = runner.IntegrationPlan('dao_it_' + 'c' * 32, runner.manifest_digest())
        with patch.object(postgres_cases, 'CASES', cases), patch.object(
                runner.IsolatedTestDatabase, 'adopt_empty',
                AsyncMock(side_effect=infra.IntegrationSafetyError('safe failure'))):
            with self.assertRaises(infra.IntegrationSafetyError):
                await runner.run_suite(config(), plan, 20000,
                    recovery_directory='unused-offline', allow_database_writes=True)
        for name, case in cases:
            case.assert_not_awaited()

    async def test_all_27_manifest_ordinals_are_bounded_and_named(self):
        from integration_diagnostics import SetupDiagnostic, SetupStage, TABLE_NAMES
        self.assertEqual(len(TABLE_NAMES), 27)
        for ordinal, name in enumerate(TABLE_NAMES, 1):
            with self.subTest(ordinal=ordinal):
                con = TransactionTraceDouble(ordinal)
                db = infra.IsolatedTestDatabase(config())
                db.schema, db._control = con.schema, con
                db._namespace_oid, db._owner, db._version = 20000, 1000, 180006
                db._setup_diagnostic = SetupDiagnostic()
                db._setup_active, db._journal = True, Mock()
                with self.assertRaises(RuntimeError):
                    await db._setup_tables(infra._table_definitions())
                self.assertEqual(db._setup_diagnostic.stage, SetupStage.CREATE_TABLE)
                self.assertEqual((db._setup_diagnostic.statement_ordinal, db._setup_diagnostic.table),
                                 (ordinal, name))

    async def test_reported_path_and_inventory_stages_are_distinct(self):
        for point, stage in (('path', 'SEARCH_PATH_SETUP'),
                ('inventory_query', 'INVENTORY_QUERY'), ('inventory_validation', 'INVENTORY_VALIDATION')):
            with self.subTest(point=point):
                con, record = await self.trace_failure(point)
                self.assertEqual(record.diagnostic['stage'], stage)
                self.assertEqual(record.diagnostic['connection_cleanup'], 'ACKNOWLEDGED')
                self.assertEqual(record.diagnostic['rollback'], 'ACKNOWLEDGED')

    async def test_rollback_error_preserves_first_failure_stage(self):
        con, record = await self.trace_failure(3, rollback_error=True)
        self.assertEqual(record.diagnostic['stage'], 'CREATE_TABLE')
        self.assertEqual(record.diagnostic['statement_ordinal'], 3)
        self.assertEqual(record.diagnostic['rollback'], 'RAISED')

    async def test_journal_checkpoint_failure_is_not_retried_and_stops_before_ddl(self):
        from integration_recovery import RecoveryJournal
        original = RecoveryJournal.save
        failed_paths = set()
        con = TransactionTraceDouble()
        def fail_checkpoint(journal, record):
            if journal.path in failed_paths:
                self.fail('Failed journal checkpoint was retried')
            if record.diagnostic and record.diagnostic['stage'] == 'CREATE_TABLE':
                failed_paths.add(journal.path)
                raise OSError('synthetic-private-disk-detail')
            return original(journal, record)
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)), patch.object(
                    RecoveryJournal, 'save', new=fail_checkpoint):
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
            record = raised.exception.recovery
            disk = json.loads((Path(directory) / (con.schema + '.json')).read_text())
        self.assertEqual(record.diagnostic['stage'], 'JOURNAL_PERSISTENCE')
        self.assertEqual(record.diagnostic['category'], 'JOURNAL_IO')
        self.assertEqual(record.diagnostic['rollback'], 'ACKNOWLEDGED')
        self.assertEqual(record.diagnostic['connection_cleanup'], 'ACKNOWLEDGED')
        self.assertEqual(con.attempts, 0)
        self.assertEqual(disk['diagnostic']['stage'], 'SEARCH_PATH_SETUP')

    async def test_precommit_checkpoint_failure_requests_rollback_without_commit(self):
        from integration_recovery import RecoveryJournal
        original = RecoveryJournal.save
        con = TransactionTraceDouble()
        def fail_checkpoint(journal, record):
            if record.diagnostic and record.diagnostic['stage'] == 'COMMIT_SETUP':
                raise OSError('synthetic-private-disk-detail')
            return original(journal, record)
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)), patch.object(
                    RecoveryJournal, 'save', new=fail_checkpoint):
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        self.assertEqual((con.commits, con.rollbacks), (0, 1))
        self.assertEqual(raised.exception.recovery.diagnostic['rollback'], 'ACKNOWLEDGED')

    async def test_cancellation_during_create_retains_diagnostic_and_closes(self):
        con = TransactionTraceDouble()
        original = con._execute
        async def cancel(sql, *args):
            if sql.startswith('CREATE TABLE '):
                raise asyncio.CancelledError()
            return await original(sql, *args)
        con.execute.side_effect = cancel
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        diagnostic = infra.setup_failure_diagnostic(raised.exception)
        self.assertEqual(diagnostic['category'], 'CANCELLED')
        self.assertEqual(diagnostic['stage'], 'CREATE_TABLE')
        self.assertEqual(diagnostic['rollback'], 'ACKNOWLEDGED')
        self.assertEqual(diagnostic['connection_cleanup'], 'ACKNOWLEDGED')
        self.assertEqual(con.rollbacks, 1)

    async def test_setup_failure_with_unconfirmed_close_preserves_recovery(self):
        con = TransactionTraceDouble(1)
        con.close.side_effect = RuntimeError('synthetic-private-close-detail')
        con.terminate.side_effect = RuntimeError('synthetic-private-terminate-detail')
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        self.assertEqual(raised.exception.recovery.phase, 'connection_close_unconfirmed')
        self.assertEqual(raised.exception.recovery.diagnostic['stage'], 'CREATE_TABLE')
        self.assertEqual(raised.exception.recovery.diagnostic['connection_cleanup'], 'UNCONFIRMED')

    async def test_initial_journal_failure_has_no_connection_or_checkpoint_retry(self):
        from integration_recovery import RecoveryJournal
        with patch.object(RecoveryJournal, '__init__', side_effect=OSError('synthetic-private-path')), patch(
                'integration_support.asyncpg.connect', AsyncMock()) as driver:
            with self.assertRaises(infra.IntegrationSafetyError) as raised:
                await infra.IsolatedTestDatabase.adopt_empty(config(), 'dao_it_' + 'f' * 32, 20000,
                    expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                    allow_database_writes=True, recovery_directory='unused')
        driver.assert_not_called()
        self.assertEqual(infra.setup_failure_diagnostic(raised.exception)['category'], 'JOURNAL_IO')

    async def test_interrupted_transaction_entry_has_unknown_verification_and_no_ddl(self):
        con = TransactionTraceDouble()
        con.transaction = Mock()
        con.transaction.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError('synthetic-private'))
        con.transaction.return_value.__aexit__ = AsyncMock()
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        diagnostic = infra.setup_failure_diagnostic(raised.exception)
        self.assertEqual((diagnostic['stage'], diagnostic['category']), ('BEGIN_SETUP', 'TIMEOUT'))
        self.assertEqual(diagnostic['rollback_verification'], 'UNKNOWN')
        self.assertEqual(con.attempts, 0)
        con.transaction.return_value.__aexit__.assert_not_awaited()

    async def test_cancellation_during_rollback_is_not_replaced_by_original_error(self):
        con = TransactionTraceDouble(1)
        original = con._execute
        async def cancel_rollback(sql, *args):
            if sql == 'ROLLBACK;':
                raise asyncio.CancelledError()
            return await original(sql, *args)
        con.execute.side_effect = cancel_rollback
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        self.assertEqual(infra.setup_failure_diagnostic(raised.exception)['rollback'], 'RAISED')
        con.close.assert_awaited_once()

    async def test_cancelled_checkpoint_requests_rollback_and_preserves_cancellation(self):
        from integration_recovery import RecoveryJournal
        original = RecoveryJournal.save
        con = TransactionTraceDouble()
        def cancel_checkpoint(journal, record):
            if record.diagnostic and record.diagnostic['stage'] == 'CREATE_TABLE':
                raise asyncio.CancelledError()
            return original(journal, record)
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)), patch.object(
                    RecoveryJournal, 'save', new=cancel_checkpoint):
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        diagnostic = infra.setup_failure_diagnostic(raised.exception)
        self.assertEqual((diagnostic['stage'], diagnostic['category']), ('JOURNAL_PERSISTENCE', 'CANCELLED'))
        self.assertEqual((con.attempts, con.rollbacks), (0, 1))

    async def test_setup_close_fallback_is_reported_separately_from_rollback(self):
        con = TransactionTraceDouble(1)
        con.close.side_effect = RuntimeError('synthetic-private-close')
        with tempfile.TemporaryDirectory(prefix='dao_offline_trace_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        diagnostic = infra.setup_failure_diagnostic(raised.exception)
        self.assertEqual(diagnostic['rollback'], 'ACKNOWLEDGED')
        self.assertEqual(diagnostic['connection_cleanup'], 'TERMINATED')
        self.assertEqual(diagnostic['rollback_verification'], 'UNKNOWN')


class DiagnosticSanitizationTests(unittest.TestCase):
    def test_wrapped_preflight_category_survives_setup_redaction(self):
        from integration_diagnostics import SetupDiagnostic, SetupStage
        db = infra.IsolatedTestDatabase(config())
        db._setup_diagnostic = SetupDiagnostic(stage=SetupStage.IDENTITY_VERIFICATION)
        db._setup_active, db._journal = True, Mock()
        db._capture_setup_failure(infra.IntegrationSafetyError('synthetic-private',
            category=infra.FailureCategory.PRIVILEGES))
        self.assertEqual(db.recovery.diagnostic['category'], 'PRIVILEGES')

    def test_untrusted_fields_never_reach_diagnostic_output(self):
        from integration_diagnostics import SetupDiagnostic, safe_setup_diagnostic
        private = 'postgresql://password SELECT private'
        diagnostic = SetupDiagnostic(stage=private, category=private, statement_ordinal=True,
            table=private, rollback=private, rollback_verification=private, connection_cleanup=private)
        self.assertNotIn(private, json.dumps(safe_setup_diagnostic(diagnostic)))
        error = infra.IntegrationSafetyError(private, setup_diagnostic={'stage': private})
        self.assertIsNone(infra.setup_failure_diagnostic(error))

    def test_only_reviewed_sqlstate_categories_are_returned(self):
        import asyncpg
        from integration_diagnostics import SetupStage, setup_category
        for kind, expected in ((asyncpg.InsufficientPrivilegeError, 'PRIVILEGES'),
                (asyncpg.ReadOnlySQLTransactionError, 'READ_ONLY'),
                (asyncpg.UndefinedColumnError, 'SQL_COMPATIBILITY'),
                (asyncpg.SerializationError, 'TRANSACTION'),
                (asyncpg.QueryCanceledError, 'TIMEOUT')):
            with self.subTest(kind=kind.__name__):
                self.assertEqual(setup_category(kind('synthetic-private'), SetupStage.CREATE_TABLE).value, expected)

    def test_runner_prints_only_sanitized_setup_diagnostic(self):
        from contextlib import redirect_stdout
        import io
        from integration_diagnostics import SetupDiagnostic, SetupStage, SetupCategory
        private = 'postgresql://password SELECT private'
        error = infra.IntegrationSafetyError(private, setup_diagnostic=SetupDiagnostic(
            stage=SetupStage.CREATE_TABLE, category=SetupCategory.PRIVILEGES,
            statement_ordinal=1, table='qi_event_settings'))
        plan = runner.IntegrationPlan('dao_it_' + 'd' * 32, runner.manifest_digest())
        with patch.object(runner, 'read_plan', return_value=plan), patch.object(
                runner.getpass, 'getpass', return_value='synthetic-password'), patch.object(
                runner, 'run_suite', AsyncMock(side_effect=error)), redirect_stdout(io.StringIO()) as output:
            code = runner.main(['run', '--plan', 'unused', '--namespace-oid', '20000', '--allow-database-writes'])
        self.assertEqual(code, 1)
        self.assertIn('CREATE_TABLE', output.getvalue())
        self.assertNotIn(private, output.getvalue())
