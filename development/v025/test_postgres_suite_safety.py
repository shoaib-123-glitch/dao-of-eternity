"""Offline runner/adoption regressions. Never execute the database-backed cases."""
import asyncio
from contextlib import asynccontextmanager, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import integration_runner as runner
import integration_support as infra
from integration_catalog import EMPTY_NAMESPACE_SQL, PRIVILEGES_SQL
from postgres_cases import CASES, ObservedConnection, _together
from test_integration_safety import environment
from test_preflight_port_forwarding import ForwardedConnectionDouble


class PreparedConnectionDouble(ForwardedConnectionDouble):
    def __init__(self):
        super().__init__('172.17.0.2')
        self.identity.update(can_create_schema=False, version=180006)
        self.server_identity['system_identifier'] = 7699864005918920747
        self.schema = 'dao_it_' + uuid4().hex
        self.namespace = {'oid': 20000, 'owner': 1000, 'acl_safe': True}
        self.empty_namespace = True

    async def _fetchrow(self, sql, *args):
        if sql == EMPTY_NAMESPACE_SQL:
            self.events.append(('read', sql))
            return {'empty_namespace': self.empty_namespace}
        return await super()._fetchrow(sql, *args)

    def checkout_copy(self):
        con = PreparedConnectionDouble()
        con.schema = self.schema
        con.namespace = deepcopy(self.namespace)
        con.tables = deepcopy(self.tables)
        return con


def config():
    return infra.TestDatabaseConfig.from_environment({
        **environment(), 'DAO_TEST_HOST': '127.0.0.1', 'DAO_TEST_PORT': '5433'})


class OfflinePlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_prepare_is_local_unique_and_contains_no_credentials_or_sql(self):
        with patch('integration_support.asyncpg.connect', new_callable=AsyncMock) as driver:
            first = runner.prepare_plan(self.directory.name)
            second = runner.prepare_plan(self.directory.name)
            plan = runner.read_plan(first)
        self.assertNotEqual(first, second)
        self.assertRegex(plan.schema, r'^dao_it_[0-9a-f]{32}$')
        self.assertEqual(plan.manifest_digest, runner.manifest_digest())
        text = first.read_text()
        for forbidden in ('password', 'dsn', 'CREATE TABLE', 'GRANT '):
            self.assertNotIn(forbidden, text)
        driver.assert_not_called()

    def test_changed_target_digest_schema_or_extra_fields_are_rejected(self):
        path = runner.prepare_plan(self.directory.name)
        original = json.loads(path.read_text())
        for mutation in ('target', 'digest', 'schema', 'extra', 'float_port', 'bool_version'):
            with self.subTest(mutation=mutation):
                payload = deepcopy(original)
                if mutation == 'target':
                    payload['target']['system_identifier'] += 1
                elif mutation == 'digest':
                    payload['manifest_digest'] = '0' * 64
                elif mutation == 'schema':
                    payload['schema'] = 'public'
                elif mutation == 'extra':
                    payload['private'] = 'synthetic-private-value'
                elif mutation == 'float_port':
                    payload['target']['port'] = 5433.0
                else:
                    payload['version'] = True
                path.write_text(json.dumps(payload))
                with self.assertRaises(infra.IntegrationSafetyError):
                    runner.read_plan(path)

    def test_environment_or_arbitrary_file_names_are_rejected_before_open(self):
        for name in ('.env', '.env.test', 'plan.json', 'public.plan.json'):
            with self.subTest(name=name), patch.object(Path, 'open', side_effect=AssertionError('must not open')):
                with self.assertRaises(infra.IntegrationSafetyError):
                    runner.read_plan(Path(self.directory.name) / name)

    def test_reviewed_case_manifest_and_clock_fixture_are_stable(self):
        self.assertEqual(tuple(name for name, _ in CASES), runner.CASE_NAMES)
        self.assertTrue(all(asyncio.iscoroutinefunction(case) for _, case in CASES))
        from heavens import events_between
        start = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        self.assertEqual(events_between(7, start, start + timedelta(hours=2)), [])


class PreparedNamespaceSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config()
        self.expected = infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.driver_patch = patch('integration_support.asyncpg.connect', new_callable=AsyncMock)
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)
        self.fresh()

    def fresh(self):
        self.con = PreparedConnectionDouble()
        self.driver.reset_mock(return_value=True, side_effect=True)
        self.driver.return_value = self.con
        return self.con

    async def adopt(self, **overrides):
        arguments = dict(expected_server=self.expected, allow_database_writes=True,
                         recovery_directory=self.directory.name)
        arguments.update(overrides)
        return await infra.IsolatedTestDatabase.adopt_empty(
            self.config, self.con.schema, 20000, **arguments)

    async def test_adoption_needs_no_database_create_and_never_creates_schema_or_grants(self):
        db = await self.adopt()
        self.assertEqual(db._state, 'ready')
        self.assertFalse(self.con.identity['can_create_schema'])
        statements = [call.args[0] for call in self.con.execute.await_args_list]
        self.assertEqual(sum(sql.startswith('CREATE TABLE ') for sql in statements), len(infra._table_definitions()))
        self.assertFalse(any(sql.startswith(('CREATE SCHEMA', 'GRANT ', 'REVOKE ', 'ALTER ')) for sql in statements))
        self.assertTrue(all(f'"{db.schema}".' in sql for sql in statements if sql.startswith('CREATE TABLE ')))
        self.assertEqual(self.con.identity['read_only'], 'on')
        self.assertEqual(sum(call.args[0] == EMPTY_NAMESPACE_SQL for call in self.con.fetchrow.await_args_list), 2)
        self.assertTrue(all(call.args[1] == 20000 for call in self.con.fetchrow.await_args_list
                            if call.args[0] == PRIVILEGES_SQL))
        self.assertEqual(self.driver.await_args.kwargs['port'], 5433)
        await db.close()
        drops = [call.args[0] for call in self.con.execute.await_args_list if call.args[0].startswith('DROP ')]
        self.assertEqual(len(drops), len(db._tables) + 1)
        self.assertTrue(all(sql.endswith(' RESTRICT') and f'"{db.schema}"' in sql for sql in drops))

    async def test_exact_write_opt_in_and_complete_server_pin_are_required(self):
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(infra.IntegrationSafetyError):
                await self.adopt(allow_database_writes=value)
        for expected in (None, {}, False):
            with self.subTest(expected=expected), self.assertRaises(infra.IntegrationSafetyError):
                await self.adopt(expected_server=expected)
        self.driver.assert_not_called()

    async def test_missing_recovery_directory_blocks_network(self):
        with self.assertRaises(infra.IntegrationSafetyError):
            await self.adopt(recovery_directory=None)
        self.driver.assert_not_called()

    async def test_invalid_schema_and_independent_oid_block_network(self):
        for schema, oid in (('public', 20000), ('dao_it_bad', 20000),
                            (self.con.schema, True), (self.con.schema, 100),
                            (self.con.schema, 2**32), (self.con.schema, '20000')):
            with self.subTest(schema=schema, oid=oid), self.assertRaises(infra.IntegrationSafetyError):
                await infra.IsolatedTestDatabase.adopt_empty(
                    self.config, schema, oid, expected_server=self.expected,
                    allow_database_writes=True, recovery_directory=self.directory.name)
        self.driver.assert_not_called()

    async def test_create_privilege_or_wrong_pg_major_is_rejected_before_writes(self):
        for field, value in (('can_create_schema', True), ('version', 170006)):
            with self.subTest(field=field):
                self.fresh().identity[field] = value
                with self.assertRaises(infra.IntegrationSafetyError):
                    await self.adopt()
                self.con.execute.assert_not_awaited()
                self.con.close.assert_awaited_once()

    async def test_mismatched_cluster_endpoint_database_and_session_never_write(self):
        for field, value in (('server_address', '127.0.0.1'), ('server_port', 5433),
                             ('database_name', 'production'), ('session_role', 'postgres'),
                             ('role_name', 'postgres'), ('read_only', 'off')):
            with self.subTest(field=field):
                self.fresh().identity[field] = value
                with self.assertRaises(infra.IntegrationSafetyError):
                    await self.adopt()
                self.con.execute.assert_not_awaited()
                self.con.close.assert_awaited_once()
        self.fresh().server_identity['system_identifier'] += 1
        with self.assertRaises(infra.IntegrationSafetyError):
            await self.adopt()
        self.con.execute.assert_not_awaited()

    async def test_wrong_namespace_oid_owner_or_acl_is_not_adopted(self):
        for field, value in (('oid', 20001), ('owner', 1001), ('acl_safe', False)):
            with self.subTest(field=field):
                self.fresh().namespace[field] = value
                with self.assertRaises(infra.IntegrationSafetyError):
                    await self.adopt()
                self.con.execute.assert_not_awaited()
                self.con.close.assert_awaited_once()

    async def test_populated_or_unknown_namespace_is_never_reused_or_removed(self):
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value):
                self.fresh().empty_namespace = value
                with self.assertRaises(infra.IntegrationSafetyError):
                    await self.adopt()
                self.con.execute.assert_not_awaited()
                self.con.close.assert_awaited_once()

    async def test_other_schema_privilege_still_rejects_and_does_not_become_an_exception(self):
        self.con.privileges['outside_schema_ownership'] = True
        with self.assertRaises(infra.IntegrationSafetyError):
            await self.adopt()
        self.con.execute.assert_not_awaited()

    async def test_existing_journal_prevents_replaying_a_plan(self):
        db = await self.adopt()
        await db.close()
        self.driver.reset_mock()
        with self.assertRaises(infra.IntegrationSafetyError):
            await self.adopt()
        self.driver.assert_not_called()

    async def test_every_checkout_rechecks_pin_and_namespace_before_enabling_writes(self):
        db = await self.adopt()
        for mutation in ('cluster', 'namespace', 'create'):
            with self.subTest(mutation=mutation):
                con = self.con.checkout_copy()
                if mutation == 'cluster':
                    con.server_identity['system_identifier'] += 1
                elif mutation == 'namespace':
                    con.namespace['oid'] += 1
                else:
                    con.identity['can_create_schema'] = True
                self.driver.return_value = con
                with self.assertRaises(infra.IntegrationSafetyError):
                    async with db.acquire():
                        self.fail('Rejected checkout was yielded')
                con.execute.assert_not_awaited()
                con.close.assert_awaited_once()
        await db.close()

    async def test_cleanup_rechecks_docker_identity_and_refuses_drops_on_drift(self):
        db = await self.adopt()
        self.con.server_identity['system_identifier'] += 1
        self.con.execute.reset_mock()
        with self.assertRaises(infra.IntegrationSafetyError):
            await db.close()
        self.con.execute.assert_not_awaited()
        self.con.close.assert_awaited_once()

    async def test_empty_namespace_is_rechecked_inside_setup_before_table_creation(self):
        original = self.con._fetchrow
        reads = 0
        async def changed(sql, *args):
            nonlocal reads
            if sql == EMPTY_NAMESPACE_SQL:
                reads += 1
                return {'empty_namespace': reads == 1}
            return await original(sql, *args)
        self.con.fetchrow.side_effect = changed
        with self.assertRaises(infra.IntegrationSafetyError):
            await self.adopt()
        self.assertFalse(any(call.args[0].startswith(('CREATE ', 'DROP '))
                             for call in self.con.execute.await_args_list))
        self.con.close.assert_awaited_once()

    async def test_partial_table_setup_failure_retains_journal_without_drop(self):
        original = self.con._execute
        async def fail_table(sql, *args):
            if sql.startswith('CREATE TABLE '):
                raise RuntimeError('synthetic-private-ddl-error')
            return await original(sql, *args)
        self.con.execute.side_effect = fail_table
        with self.assertRaises(infra.IntegrationSafetyError) as raised:
            await self.adopt()
        self.assertEqual(raised.exception.recovery.phase, 'creating_tables')
        self.assertEqual(raised.exception.recovery.outcome, 'rollback_unverified')
        self.assertNotIn('synthetic-private-ddl-error', str(raised.exception))
        self.assertFalse(any(call.args[0].startswith('DROP ') for call in self.con.execute.await_args_list))
        self.assertEqual(len(list(Path(self.directory.name).glob('*.json'))), 1)
        self.con.close.assert_awaited_once()

    async def test_cancelled_prepared_setup_closes_and_retains_recovery(self):
        original = self.con._fetchrow
        async def cancel_empty(sql, *args):
            if sql == EMPTY_NAMESPACE_SQL:
                raise asyncio.CancelledError()
            return await original(sql, *args)
        self.con.fetchrow.side_effect = cancel_empty
        with self.assertRaises(asyncio.CancelledError) as raised:
            await self.adopt()
        self.assertEqual(raised.exception.recovery.namespace_oid, 20000)
        self.con.execute.assert_not_awaited()
        self.con.close.assert_awaited_once()

    async def test_prepared_inventory_drift_blocks_destructive_cleanup(self):
        db = await self.adopt()
        self.con.tables[0]['oid'] += 1
        self.con.execute.reset_mock()
        with self.assertRaises(infra.IntegrationSafetyError):
            await db.close()
        self.assertFalse(any(call.args[0].startswith('DROP ') for call in self.con.execute.await_args_list))
        self.con.close.assert_awaited_once()


class RunnerSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_api_gate_precedes_manifest_connections_and_cases(self):
        with patch('integration_runner.manifest_digest', side_effect=AssertionError('manifest read')), \
                patch('integration_runner.IsolatedTestDatabase.adopt_empty', new_callable=AsyncMock) as adopt:
            for value in (False, None, 1):
                with self.subTest(value=value), self.assertRaises(infra.IntegrationSafetyError):
                    await runner.run_suite({}, None, 20000, recovery_directory=None,
                                           allow_database_writes=value)
        adopt.assert_not_called()

    def test_default_cli_and_missing_write_flag_do_not_read_files_or_prompt(self):
        with patch('integration_runner.getpass.getpass') as password, \
                patch.object(Path, 'open', side_effect=AssertionError('file read')):
            for args in ([], ['run', '--plan', '.env.test', '--namespace-oid', '20000']):
                with self.subTest(args=args), redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(runner.main(args), 2)
                    self.assertIn('WRITES_DISABLED', output.getvalue())
        password.assert_not_called()

    async def test_unreviewed_client_target_cannot_reach_adoption(self):
        plan = runner.IntegrationPlan('dao_it_' + 'a' * 32, runner.manifest_digest())
        with patch('integration_runner.IsolatedTestDatabase.adopt_empty', new_callable=AsyncMock) as adopt:
            for change in ({'host': 'localhost'}, {'port': 5432}):
                with self.subTest(change=change), self.assertRaises(infra.IntegrationSafetyError):
                    await runner.run_suite(replace(config(), **change), plan, 20000,
                                           recovery_directory=None, allow_database_writes=True)
        adopt.assert_not_called()

    async def test_invalid_independent_oid_cannot_reach_adoption(self):
        plan = runner.IntegrationPlan('dao_it_' + 'd' * 32, runner.manifest_digest())
        with patch('integration_runner.IsolatedTestDatabase.adopt_empty', new_callable=AsyncMock) as adopt:
            for oid in (None, True, 100, 20000.0, 2**32):
                with self.subTest(oid=oid), self.assertRaises(infra.IntegrationSafetyError):
                    await runner.run_suite(config(), plan, oid, recovery_directory=None,
                                           allow_database_writes=True)
        adopt.assert_not_called()

    def test_password_prompt_refuses_echo_fallback_before_running_suite(self):
        def no_echo_console(prompt):
            warnings.warn('synthetic-console-warning', runner.getpass.GetPassWarning)
            self.fail('Echo fallback was allowed')
        plan = runner.IntegrationPlan('dao_it_' + 'e' * 32, 'synthetic-digest')
        with patch('integration_runner.read_plan', return_value=plan), \
                patch('integration_runner.getpass.getpass', side_effect=no_echo_console), \
                patch('integration_runner.run_suite', new_callable=AsyncMock) as suite, \
                redirect_stdout(io.StringIO()) as output:
            result = runner.main(['run', '--plan', 'synthetic-plan', '--namespace-oid', '20000',
                                  '--allow-database-writes'])
        self.assertEqual(result, 1)
        self.assertNotIn('synthetic-console-warning', output.getvalue())
        suite.assert_not_called()

    async def test_runner_calls_only_fixed_cases_and_always_closes(self):
        plan = runner.IntegrationPlan('dao_it_' + 'b' * 32, runner.manifest_digest())
        db = SimpleNamespace(close=AsyncMock())
        cases = tuple((name, AsyncMock()) for name in runner.CASE_NAMES)
        with patch('postgres_cases.CASES', cases), \
                patch('integration_runner.IsolatedTestDatabase.adopt_empty', AsyncMock(return_value=db)) as adopt:
            result = await runner.run_suite(config(), plan, 20000, recovery_directory='synthetic-directory',
                                             allow_database_writes=True)
        self.assertEqual(result['status'], 'PASSED')
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(adopt.await_args.kwargs['expected_server'],
                         infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747))
        self.assertTrue(adopt.await_args.kwargs['allow_database_writes'])
        for _, case in cases:
            case.assert_awaited_once_with(db)
        db.close.assert_awaited_once()

    async def test_case_failure_is_redacted_not_retried_and_stops_later_cases(self):
        plan = runner.IntegrationPlan('dao_it_' + 'c' * 32, runner.manifest_digest())
        db = SimpleNamespace(close=AsyncMock())
        cases = tuple((name, AsyncMock()) for name in runner.CASE_NAMES)
        cases[0][1].side_effect = RuntimeError('synthetic-password SELECT private_row')
        with patch('postgres_cases.CASES', cases), \
                patch('integration_runner.IsolatedTestDatabase.adopt_empty', AsyncMock(return_value=db)):
            result = await runner.run_suite(config(), plan, 20000, recovery_directory='synthetic-directory',
                                             allow_database_writes=True)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['cases'], [{'case': runner.CASE_NAMES[0], 'passed': False}])
        self.assertNotIn('synthetic-password', json.dumps(result))
        self.assertNotIn('SELECT', json.dumps(result))
        cases[0][1].assert_awaited_once()
        for _, case in cases[1:]:
            case.assert_not_called()
        db.close.assert_awaited_once()

    async def test_runner_cancellation_still_closes_and_preserves_cancellation(self):
        plan = runner.IntegrationPlan('dao_it_' + 'f' * 32, runner.manifest_digest())
        db = SimpleNamespace(close=AsyncMock())
        cases = tuple((name, AsyncMock()) for name in runner.CASE_NAMES)
        cases[0][1].side_effect = asyncio.CancelledError()
        with patch('postgres_cases.CASES', cases), \
                patch('integration_runner.IsolatedTestDatabase.adopt_empty', AsyncMock(return_value=db)):
            with self.assertRaises(asyncio.CancelledError):
                await runner.run_suite(config(), plan, 20000, recovery_directory='synthetic-directory',
                                       allow_database_writes=True)
        db.close.assert_awaited_once()

    async def test_observer_forwards_results_and_actual_transaction_object(self):
        transaction = object()
        con = SimpleNamespace(fetchrow=AsyncMock(return_value={'actual': True}),
                              transaction=Mock(return_value=transaction))
        after = AsyncMock()
        wrapped = ObservedConnection(con, after)
        result = await wrapped.fetchrow('synthetic-query', 7)
        self.assertEqual(result, {'actual': True})
        self.assertIs(wrapped.transaction(), transaction)
        con.fetchrow.assert_awaited_once_with('synthetic-query', 7)
        after.assert_awaited_once_with('fetchrow', 'synthetic-query', (7,), con)

    async def test_concurrent_helper_drains_other_workers_after_failure(self):
        active = 0
        class FakeDatabase:
            @asynccontextmanager
            async def acquire(self):
                nonlocal active
                active += 1
                try:
                    yield object()
                finally:
                    active -= 1
        async def failure(repo):
            async with repo.pool.acquire():
                raise ValueError('synthetic-domain-error')
        async def pending(repo):
            async with repo.pool.acquire():
                await asyncio.Event().wait()
        with self.assertRaises(ValueError):
            await _together(FakeDatabase(), failure, pending)
        self.assertEqual(active, 0)


if __name__ == '__main__':
    unittest.main()
